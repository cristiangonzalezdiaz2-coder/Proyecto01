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
from ..notifications import TelegramNotifier
from ..persistence import PositionStore
from ..risk import Position, RiskManager
from ..strategies import Signal, load_strategy

log = get_logger("engine")


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
        self.store = PositionStore(config.db_path)

        # Precisión y mínimos del símbolo (para no enviar órdenes inválidas).
        self.symbol_info = self._load_symbol_info()

        # Restaurar estado de una sesión anterior (posiciones abiertas + PnL diario).
        self._restore_state()

        self.mode_label = "LIVE (dinero real)" if self.live else "PAPER (simulación)"
        log.info("Motor iniciado | modo=%s | símbolo=%s | estrategia=%s",
                 self.mode_label, config.symbol, self.strategy.name)
        recovered = len(self.risk.open_positions)
        self.notifier.send(
            f"🤖 <b>Bot MEXC iniciado</b>\n"
            f"Modo: {self.mode_label}\n"
            f"Símbolo: {config.symbol}\n"
            f"Estrategia: {self.strategy.name}\n"
            f"Posiciones recuperadas: {recovered}"
        )

    # ------------------------------------------------------------------
    def _load_symbol_info(self):
        """Obtiene la precisión del símbolo. Si falla (p. ej. sin red), el bot
        sigue funcionando sin ajuste de precisión (solo se avisa)."""
        try:
            info = self.client.get_symbol_info(self.config.symbol)
            log.info("Precisión %s | cantidad: %d dec | precio: %d dec | "
                     "mín. mercado: %s %s | mín. base: %s",
                     info.symbol, info.base_precision, info.quote_precision,
                     info.min_quote_amount_market, info.quote_asset, info.min_base_size)
            if not info.trading_allowed:
                log.warning("El par %s no admite trading spot ahora mismo.", info.symbol)
            return info
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo cargar la precisión de %s (%s). "
                        "Se opera sin ajuste de precisión.", self.config.symbol, exc)
            return None

    def _apply_precision(self, position: Position) -> None:
        """Ajusta cantidad y precios de la posición a la precisión del símbolo."""
        if self.symbol_info is None:
            return
        position.quantity = self.symbol_info.floor_quantity(position.quantity)
        position.stop_loss = self.symbol_info.round_price(position.stop_loss)
        position.take_profit = self.symbol_info.round_price(position.take_profit)

    # ------------------------------------------------------------------
    def _restore_state(self) -> None:
        """Carga posiciones abiertas y el estado diario desde la base de datos."""
        self.risk.open_positions = self.store.load_open_positions()
        today = _today_str()
        state = self.store.load_daily_state(today)
        if state is not None:
            self.risk.daily_pnl, self.risk.halted = state
            log.info("Estado diario restaurado: PnL=%.4f, bloqueado=%s",
                     self.risk.daily_pnl, self.risk.halted)

    # ------------------------------------------------------------------
    def _market_buy(self, price: float) -> Position | None:
        quote_amount = self.config.risk.quote_per_trade

        # Validar el importe contra el mínimo del exchange (si lo conocemos).
        if self.symbol_info is not None:
            quote_amount, err = self.symbol_info.check_market_buy(quote_amount)
            if err:
                log.warning("Compra omitida: %s", err)
                self.notifier.send(f"⚠️ Compra omitida en {self.config.symbol}: {err}")
                return None

        position = self.risk.build_position(self.config.symbol, price)
        self._apply_precision(position)  # ajustar cantidad y precios

        if self.live:
            resp = self.client.new_order(
                symbol=self.config.symbol,
                side="BUY",
                order_type="MARKET",
                quote_order_qty=quote_amount,
            )
            log.info("Orden BUY real enviada: %s", resp)
        else:
            log.info("[PAPER] Compra simulada %.8f @ %.2f (SL %.2f / TP %.2f)",
                     position.quantity, price, position.stop_loss, position.take_profit)
        self.risk.register_open(position)
        self.store.add_position(position)  # persistir la posición abierta
        self.notifier.send(
            f"🟢 <b>COMPRA</b> ({self.mode_label})\n"
            f"{self.config.symbol}\n"
            f"Precio: <b>{price:.2f}</b>\n"
            f"Cantidad: {position.quantity:.8f}\n"
            f"Stop-loss: {position.stop_loss:.2f}\n"
            f"Take-profit: {position.take_profit:.2f}"
        )
        return position

    def _market_sell(self, position: Position, price: float, reason: str) -> None:
        if self.live:
            # Ajustar la cantidad a la precisión del símbolo antes de vender.
            sell_qty = position.quantity
            if self.symbol_info is not None:
                sell_qty, err = self.symbol_info.check_sell_qty(sell_qty)
                if err:
                    log.error("No se pudo vender %s: %s", self.config.symbol, err)
                    self.notifier.send(
                        f"⚠️ No se pudo cerrar {self.config.symbol}: {err}"
                    )
                    return
            resp = self.client.new_order(
                symbol=self.config.symbol,
                side="SELL",
                order_type="MARKET",
                quantity=sell_qty,
            )
            log.info("Orden SELL real enviada (%s): %s", reason, resp)
        pnl = self.risk.register_close(position, price)
        # Persistir el cierre y el nuevo estado diario.
        self.store.close_position(position, price, pnl, reason)
        self.store.save_daily_state(_today_str(), self.risk.daily_pnl, self.risk.halted)
        log.info("[%s] Cierre por %s @ %.2f | PnL=%.4f | PnL diario=%.4f",
                 "LIVE" if self.live else "PAPER", reason, price, pnl, self.risk.daily_pnl)

        emoji = "✅" if pnl >= 0 else "🔴"
        reasons_es = {
            "stop_loss": "Stop-loss",
            "take_profit": "Take-profit",
            "signal_sell": "Señal de venta",
        }
        self.notifier.send(
            f"{emoji} <b>VENTA</b> ({self.mode_label})\n"
            f"{self.config.symbol}\n"
            f"Motivo: {reasons_es.get(reason, reason)}\n"
            f"Precio: <b>{price:.2f}</b>\n"
            f"PnL operación: <b>{pnl:+.4f}</b>\n"
            f"PnL del día: {self.risk.daily_pnl:+.4f}"
        )

        if self.risk.halted:
            log.warning("Límite de pérdida diaria alcanzado. El bot deja de abrir posiciones.")
            self.notifier.send(
                "⛔ <b>Límite de pérdida diaria alcanzado</b>\n"
                "El bot deja de abrir nuevas posiciones hasta mañana."
            )

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
                    self.store.save_daily_state(
                        today.isoformat(), self.risk.daily_pnl, self.risk.halted
                    )
                    current_day = today
                    log.info("Nuevo día: contador de pérdidas reiniciado.")

                try:
                    self._step()
                except Exception as exc:  # noqa: BLE001 - no queremos que un fallo puntual mate el bot
                    log.error("Error en el ciclo: %s", exc)

                time.sleep(self.config.poll_seconds)
        except KeyboardInterrupt:
            log.info("Detenido por el usuario. PnL de la sesión: %.4f", self.risk.daily_pnl)
        finally:
            self.store.close()
