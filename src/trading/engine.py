"""Motor de trading: une datos de mercado, estrategia, riesgo y ejecución.

Soporta dos modos:
  - paper: simula las órdenes en memoria (no toca dinero real).
  - live:  envía órdenes reales a MEXC.
"""
import time
from datetime import datetime, timezone

import pandas as pd

from ..config import AppConfig
from ..logger import get_logger
from ..mexc import MexcSpotClient
from ..risk import Position, RiskManager
from ..strategies import Signal, load_strategy

log = get_logger("engine")


def klines_to_df(raw: list[list]) -> pd.DataFrame:
    """Convierte la respuesta de klines de MEXC en un DataFrame OHLCV."""
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_vol"]
    df = pd.DataFrame(raw, columns=cols[: len(raw[0])])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


class TradingEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = MexcSpotClient(config.api_key, config.api_secret)
        self.strategy = load_strategy(config.strategy.name, config.strategy.params)
        self.risk = RiskManager(config.risk)
        self.live = config.trading_mode == "live"

        mode = "LIVE (dinero real)" if self.live else "PAPER (simulación)"
        log.info("Motor iniciado | modo=%s | símbolo=%s | estrategia=%s",
                 mode, config.symbol, self.strategy.name)

    # ------------------------------------------------------------------
    def _market_buy(self, price: float) -> Position:
        position = self.risk.build_position(self.config.symbol, price)
        if self.live:
            resp = self.client.new_order(
                symbol=self.config.symbol,
                side="BUY",
                order_type="MARKET",
                quote_order_qty=self.config.risk.quote_per_trade,
            )
            log.info("Orden BUY real enviada: %s", resp)
        else:
            log.info("[PAPER] Compra simulada %.6f @ %.2f (SL %.2f / TP %.2f)",
                     position.quantity, price, position.stop_loss, position.take_profit)
        self.risk.register_open(position)
        return position

    def _market_sell(self, position: Position, price: float, reason: str) -> None:
        if self.live:
            resp = self.client.new_order(
                symbol=self.config.symbol,
                side="SELL",
                order_type="MARKET",
                quantity=round(position.quantity, 6),
            )
            log.info("Orden SELL real enviada (%s): %s", reason, resp)
        pnl = self.risk.register_close(position, price)
        log.info("[%s] Cierre por %s @ %.2f | PnL=%.4f | PnL diario=%.4f",
                 "LIVE" if self.live else "PAPER", reason, price, pnl, self.risk.daily_pnl)
        if self.risk.halted:
            log.warning("Límite de pérdida diaria alcanzado. El bot deja de abrir posiciones.")

    # ------------------------------------------------------------------
    def _step(self) -> None:
        """Un ciclo: obtener datos, evaluar salidas y luego entradas."""
        raw = self.client.get_klines(
            self.config.symbol, self.config.interval, limit=200
        )
        df = klines_to_df(raw)
        price = float(df["close"].iloc[-1])

        # 1) Revisar salidas de posiciones abiertas (SL/TP).
        for position in list(self.risk.open_positions):
            reason = self.risk.should_close(position, price)
            if reason:
                self._market_sell(position, price, reason)

        # 2) Evaluar la estrategia para nuevas entradas.
        signal = self.strategy.generate_signal(df)
        log.info("Precio=%.2f | señal=%s | posiciones=%d",
                 price, signal.value, len(self.risk.open_positions))

        if signal == Signal.BUY and self.risk.can_open():
            self._market_buy(price)
        elif signal == Signal.SELL:
            # Cierre por señal de estrategia (además de SL/TP).
            for position in list(self.risk.open_positions):
                self._market_sell(position, price, "signal_sell")

    def run(self) -> None:
        """Bucle principal. Ctrl+C para detener."""
        log.info("Comprobando conectividad con MEXC...")
        self.client.ping()
        log.info("Conectado. Iniciando bucle (cada %ds).", self.config.poll_seconds)

        current_day = datetime.now(timezone.utc).date()
        try:
            while True:
                today = datetime.now(timezone.utc).date()
                if today != current_day:
                    self.risk.reset_daily()
                    current_day = today
                    log.info("Nuevo día: contador de pérdidas reiniciado.")

                try:
                    self._step()
                except Exception as exc:  # noqa: BLE001 - no queremos que un fallo puntual mate el bot
                    log.error("Error en el ciclo: %s", exc)

                time.sleep(self.config.poll_seconds)
        except KeyboardInterrupt:
            log.info("Detenido por el usuario. PnL de la sesión: %.4f", self.risk.daily_pnl)
