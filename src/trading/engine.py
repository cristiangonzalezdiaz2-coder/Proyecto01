"""Motor de trading: une datos de mercado, estrategia, riesgo y ejecución.

Cada instancia opera UN bot (un par + una estrategia). Varios motores pueden
correr en paralelo compartiendo la misma base de datos (ver MultiRunner);
cada uno etiqueta sus filas con el nombre de su bot.

Soporta dos modos:
  - paper: simula las órdenes en memoria (no toca dinero real).
  - live:  envía órdenes reales a MEXC.
"""
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from ..config import AppConfig, BotConfig, GlobalRiskConfig
from ..logger import get_logger
from ..mexc import MexcError, MexcSpotClient
from ..notifications import TelegramNotifier
from ..persistence import PositionStore
from ..risk import GlobalRiskManager, Position, RiskManager
from ..strategies import Signal, load_strategy

log = get_logger("engine")


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# Estados en los que la orden ya no va a ejecutar nada más.
_FAILED_STATUSES = {"CANCELED", "PARTIALLY_CANCELED", "REJECTED", "EXPIRED"}


@dataclass
class OrderOutcome:
    """Resultado verificado de una orden: estado y ejecución real."""
    status: str = ""              # último estado conocido ("" si no se pudo saber)
    avg_price: float | None = None
    executed_qty: float = 0.0

    @property
    def filled(self) -> bool:
        """Hay ejecución real (total o parcial) con precio medio conocido."""
        return self.avg_price is not None and self.executed_qty > 0

    @property
    def failed(self) -> bool:
        """La orden terminó sin ejecutar NADA: no hay nada que registrar."""
        return not self.filled and self.status in _FAILED_STATUSES

    @property
    def partial(self) -> bool:
        """Terminó ejecutando solo una parte (el resto se canceló)."""
        return self.filled and self.status == "PARTIALLY_CANCELED"


def resolve_order_outcome(client: MexcSpotClient, symbol: str, resp: dict,
                          attempts: int = 5, delay: float = 0.5) -> OrderOutcome:
    """Verifica el resultado real de una orden: estado, precio medio y cantidad.

    Primero mira la respuesta inmediata; si aún no trae la ejecución (las
    órdenes MARKET de MEXC pueden responder antes de ejecutarse), consulta la
    orden hasta `attempts` veces. Deja de consultar en cuanto hay fill o la
    orden alcanza un estado terminal fallido (cancelada/rechazada). Si tras los
    intentos no se sabe nada, devuelve un OrderOutcome vacío (ni filled ni
    failed): el llamador debe usar su estimación y avisar.
    """
    def _parse(data: dict) -> OrderOutcome:
        status = str(data.get("status") or "").upper()
        try:
            qty = float(data.get("executedQty") or 0)
            quote = float(data.get("cummulativeQuoteQty") or 0)
        except (TypeError, ValueError):
            qty = quote = 0.0
        if qty > 0 and quote > 0:
            return OrderOutcome(status=status, avg_price=quote / qty, executed_qty=qty)
        return OrderOutcome(status=status)

    outcome = _parse(resp)
    order_id = resp.get("orderId")
    for _ in range(attempts):
        if outcome.filled or outcome.failed or order_id is None:
            break
        time.sleep(delay)
        try:
            outcome = _parse(client.query_order(symbol, order_id))
        except MexcError as exc:
            log.warning("No se pudo consultar la orden %s de %s: %s", order_id, symbol, exc)
    return outcome


def klines_to_df(raw: list[list]) -> pd.DataFrame:
    """Convierte la respuesta de klines de MEXC en un DataFrame OHLCV."""
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_vol"]
    df = pd.DataFrame(raw, columns=cols[: len(raw[0])])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


class TradingEngine:
    def __init__(self, config: AppConfig, bot: BotConfig | None = None,
                 global_risk: GlobalRiskManager | None = None):
        self.config = config
        self.bot = bot or config.bots[0]
        self.name = self.bot.name
        self.symbol = self.bot.symbol

        self.client = MexcSpotClient(config.api_key, config.api_secret)
        self.strategy = load_strategy(self.bot.strategy.name, self.bot.strategy.params)
        self.risk = RiskManager(self.bot.risk)
        # Riesgo global compartido (deshabilitado si no se pasa uno).
        self.global_risk = global_risk or GlobalRiskManager(GlobalRiskConfig())
        self.live = config.trading_mode == "live"
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
        self.store = PositionStore(config.db_path)

        # Precisión y mínimos del símbolo (para no enviar órdenes inválidas).
        self.symbol_info = self._load_symbol_info()

        # open_time de la última vela CERRADA ya evaluada por la estrategia
        # (cada vela se evalúa una sola vez, aunque el poll sea más frecuente).
        self._last_signal_candle = None

        # Intentos de cierre fallidos por cantidad invendible, por posición
        # (tras DUST_CLOSE_AFTER se retira la posición como polvo).
        self._close_failures: dict = {}

        # Restaurar estado de una sesión anterior (posiciones abiertas + PnL diario).
        self._restore_state()
        # En live, comprobar que el balance real respalda lo restaurado.
        self._reconcile_live_positions()

        self.mode_label = "LIVE (dinero real)" if self.live else "PAPER (simulación)"
        log.info("[%s] Motor iniciado | modo=%s | símbolo=%s | estrategia=%s",
                 self.name, self.mode_label, self.symbol, self.strategy.name)
        recovered = len(self.risk.open_positions)
        self.notifier.send(
            f"🤖 <b>Bot iniciado: {self.name}</b>\n"
            f"Modo: {self.mode_label}\n"
            f"Símbolo: {self.symbol}\n"
            f"Estrategia: {self.strategy.name}\n"
            f"Posiciones recuperadas: {recovered}"
        )

    # ------------------------------------------------------------------
    def _load_symbol_info(self):
        """Obtiene la precisión del símbolo. Si falla (p. ej. sin red), el bot
        sigue funcionando sin ajuste de precisión (solo se avisa)."""
        try:
            info = self.client.get_symbol_info(self.symbol)
            log.info("[%s] Precisión %s | cantidad: %d dec | precio: %d dec | "
                     "mín. mercado: %s %s | mín. base: %s",
                     self.name, info.symbol, info.base_precision, info.quote_precision,
                     info.min_quote_amount_market, info.quote_asset, info.min_base_size)
            if not info.trading_allowed:
                log.warning("[%s] El par %s no admite trading spot ahora mismo.",
                            self.name, info.symbol)
            return info
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] No se pudo cargar la precisión de %s (%s). "
                        "Se opera sin ajuste de precisión.", self.name, self.symbol, exc)
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
        """Carga posiciones abiertas y el estado diario de ESTE bot."""
        self.risk.open_positions = self.store.load_open_positions(self.name)
        state = self.store.load_daily_state(_today_str(), self.name)
        if state is not None:
            self.risk.daily_pnl, self.risk.halted = state
            log.info("[%s] Estado diario restaurado: PnL=%.4f, bloqueado=%s",
                     self.name, self.risk.daily_pnl, self.risk.halted)

    def _reconcile_live_positions(self) -> None:
        """(live) Verifica que el balance del exchange respalda las posiciones
        restauradas de la BD. Si falta saldo (se vendió a mano, otra app, etc.),
        reduce la cantidad o descarta la posición, avisando siempre.

        Cuenta el saldo libre MÁS el bloqueado (las órdenes TP nuestras retienen
        saldo). Nota: si varios bots operan el mismo par, cada uno reconcilia
        contra el balance común y podría haber doble conteo."""
        if not self.live or not self.risk.open_positions:
            return
        if self.symbol_info is None:
            log.warning("[%s] Sin info del símbolo: no se puede reconciliar el balance.",
                        self.name)
            return
        base = self.symbol_info.base_asset
        try:
            free, locked = self.client.get_asset_balance(base)
        except Exception as exc:  # noqa: BLE001 - no impedir el arranque
            log.warning("[%s] No se pudo leer el balance de %s para reconciliar: %s",
                        self.name, base, exc)
            return

        available = free + locked
        adjustments: list[str] = []
        for position in sorted(list(self.risk.open_positions), key=lambda p: p.id or 0):
            if available >= position.quantity * (1 - 1e-9):
                available -= position.quantity
                continue
            backed = self.symbol_info.floor_quantity(max(available, 0.0))
            if backed > 0:
                adjustments.append(
                    f"posición {position.id}: cantidad {position.quantity:.8f} -> "
                    f"{backed:.8f} {base} (saldo insuficiente)"
                )
                log.warning("[%s] Reconciliación: la posición %s se reduce de %.8f a "
                            "%.8f %s (saldo insuficiente).", self.name, position.id,
                            position.quantity, backed, base)
                position.quantity = backed
                self.store.update_position_quantity(position.id, backed)
                available = 0.0
            else:
                adjustments.append(
                    f"posición {position.id}: descartada ({position.quantity:.8f} {base} "
                    f"sin respaldo en el balance)"
                )
                log.error("[%s] Reconciliación: la posición %s (%.8f %s) no tiene "
                          "respaldo en el balance; se descarta del seguimiento.",
                          self.name, position.id, position.quantity, base)
                # Cierre administrativo: sin venta real, PnL 0 y motivo propio.
                self.store.close_position(position, position.entry_price, 0.0,
                                          "reconcile", bot=self.name)
                self.risk.open_positions.remove(position)
                self.global_risk.register_close(position, 0.0)
        if adjustments:
            self.notifier.send(
                f"⚠️ <b>[{self.name}] Reconciliación de balances</b>\n"
                f"El balance de {base} no respalda las posiciones restauradas:\n- "
                + "\n- ".join(adjustments)
            )
        else:
            log.info("[%s] Reconciliación OK: el balance de %s respalda las %d "
                     "posición(es) restaurada(s).", self.name, base,
                     len(self.risk.open_positions))

    # ------------------------------------------------------------------
    def _available_quote(self) -> float | None:
        """Balance (moneda cotizada) disponible para dimensionar una compra.

        En live es el saldo libre real del exchange. En paper es el capital
        simulado (paper_balance) más el PnL realizado del bot, menos lo ya
        comprometido en posiciones abiertas. None si no se puede conocer."""
        if self.live:
            if self.symbol_info is None:
                log.warning("[%s] Sin info del símbolo: sizing con quote_per_trade fijo.",
                            self.name)
                return None
            try:
                return self.client.get_balance(self.symbol_info.quote_asset)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] No se pudo leer el balance para el sizing (%s); "
                            "se usa quote_per_trade fijo.", self.name, exc)
                return None
        equity = self.bot.risk.paper_balance + self.store.fetch_bot_realized_pnl(self.name)
        committed = sum(p.entry_price * p.quantity for p in self.risk.open_positions)
        return max(equity - committed, 0.0)

    def _sellable_qty(self, qty: float) -> float:
        """(live) Limita una cantidad a vender al saldo libre real del activo base.

        MEXC cobra la comisión de las compras MARKET en el activo comprado, así
        que el saldo real queda ligeramente por debajo del executedQty de la
        compra; ofrecer o vender la cantidad completa sería rechazado por saldo
        insuficiente. Si no se puede leer el balance, se usa la cantidad pedida."""
        if self.symbol_info is None:
            return qty
        base = self.symbol_info.base_asset
        try:
            free = self.client.get_balance(base)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] No se pudo leer el saldo libre de %s (%s); "
                        "se usa la cantidad completa.", self.name, base, exc)
            return qty
        if free < qty:
            capped = self.symbol_info.floor_quantity(free)
            log.info("[%s] Cantidad ajustada al saldo libre: %.8f -> %.8f %s "
                     "(comisión cobrada en el activo base).",
                     self.name, qty, capped, base)
            return capped
        return qty

    def _place_tp_order(self, position: Position) -> None:
        """(live) Coloca el take-profit como orden LIMIT en el exchange.

        Así el TP se ejecuta aunque el bot esté caído o sin red. MEXC spot v3
        no admite órdenes stop, por lo que el stop-loss no puede delegarse al
        exchange y se sigue vigilando localmente en cada ciclo. Si la orden no
        se puede colocar, el TP también se vigilará localmente (fallback)."""
        qty = self._sellable_qty(position.quantity)
        tp_price = position.take_profit
        if self.symbol_info is not None:
            qty, err = self.symbol_info.check_sell_qty(qty)
            if err:
                log.warning("[%s] No se coloca TP en el exchange (%s); "
                            "se vigilará localmente.", self.name, err)
                return
            tp_price = self.symbol_info.round_price(tp_price)
        try:
            resp = self.client.new_order(
                symbol=self.symbol,
                side="SELL",
                order_type="LIMIT",
                quantity=qty,
                price=tp_price,
            )
        except MexcError as exc:
            log.warning("[%s] No se pudo colocar el TP en el exchange (%s); "
                        "se vigilará localmente.", self.name, exc)
            return
        order_id = str(resp.get("orderId") or "")
        if not order_id:
            log.warning("[%s] El exchange no devolvió id para la orden TP; "
                        "se vigilará localmente.", self.name)
            return
        position.tp_order_id = order_id
        self.store.set_tp_order(position.id, order_id)
        log.info("[%s] TP colocado en el exchange: orden %s (%.8f @ %.2f)",
                 self.name, order_id, qty, tp_price)

    def _cancel_tp_order(self, position: Position) -> tuple[float, float] | None:
        """Cancela la orden TP del exchange para liberar su saldo antes de vender.

        Devuelve (cantidad, importe) ya ejecutados por esa orden antes de la
        cancelación (0, 0 si nada), o None si no se pudo cancelar ni averiguar
        su estado (el llamador debe reintentar en el próximo ciclo)."""
        order_id = position.tp_order_id
        try:
            self.client.cancel_order(self.symbol, order_id)
        except MexcError as exc:
            # Pudo fallar porque la orden ya se ejecutó por completo.
            try:
                data = self.client.query_order(self.symbol, order_id)
            except MexcError:
                log.warning("[%s] No se pudo cancelar ni consultar la orden TP %s (%s); "
                            "se reintentará.", self.name, order_id, exc)
                return None
            status = str(data.get("status") or "").upper()
            if status not in ("FILLED", *_FAILED_STATUSES):
                log.warning("[%s] No se pudo cancelar la orden TP %s (estado %s); "
                            "se reintentará.", self.name, order_id, status)
                return None
        # Tras cancelar (o descubrir que ya terminó), mirar cuánto ejecutó.
        executed_qty = executed_quote = 0.0
        try:
            data = self.client.query_order(self.symbol, order_id)
            executed_qty = float(data.get("executedQty") or 0)
            executed_quote = float(data.get("cummulativeQuoteQty") or 0)
        except MexcError:
            pass  # sin datos: asumimos que no ejecutó nada
        position.tp_order_id = None
        self.store.set_tp_order(position.id, None)
        return executed_qty, executed_quote

    def _check_exchange_tp(self) -> None:
        """(live) Detecta órdenes TP del exchange ya ejecutadas (quizá mientras
        el bot estaba caído) y contabiliza sus cierres."""
        for position in list(self.risk.open_positions):
            if not position.tp_order_id:
                continue
            try:
                data = self.client.query_order(self.symbol, position.tp_order_id)
            except MexcError as exc:
                log.warning("[%s] No se pudo consultar la orden TP %s: %s",
                            self.name, position.tp_order_id, exc)
                continue
            status = str(data.get("status") or "").upper()
            if status == "FILLED":
                qty = float(data.get("executedQty") or 0)
                quote = float(data.get("cummulativeQuoteQty") or 0)
                exit_price = quote / qty if qty > 0 and quote > 0 else position.take_profit
                position.tp_order_id = None
                self.store.set_tp_order(position.id, None)
                self._finalize_close(position, exit_price, "take_profit",
                                     "\n(ejecutado por la orden TP del exchange)")
            elif status in _FAILED_STATUSES:
                # La orden desapareció (p. ej. cancelada a mano): recolocarla.
                log.warning("[%s] La orden TP %s ya no está activa (%s); se recoloca.",
                            self.name, position.tp_order_id, status)
                position.tp_order_id = None
                self.store.set_tp_order(position.id, None)
                self._place_tp_order(position)

    # ------------------------------------------------------------------
    def _market_buy(self, price: float) -> Position | None:
        # Dimensionar la compra (fijo o dinámico según `sizing`).
        if self.bot.risk.sizing == "fixed":
            quote_amount = self.bot.risk.quote_per_trade
        else:
            quote_amount = self.risk.position_size(self._available_quote())
            log.info("[%s] Sizing %s: importe %.2f", self.name,
                     self.bot.risk.sizing, quote_amount)

        # Validar el importe contra el mínimo del exchange (si lo conocemos).
        if self.symbol_info is not None:
            quote_amount, err = self.symbol_info.check_market_buy(quote_amount)
            if err:
                log.warning("[%s] Compra omitida: %s", self.name, err)
                self.notifier.send(f"⚠️ [{self.name}] Compra omitida en {self.symbol}: {err}")
                return None

        # Reservar cupo en el riesgo GLOBAL de forma atómica (comprobar y
        # apartar en una sola operación evita que dos bots excedan el límite).
        ok, gerr = self.global_risk.reserve(quote_amount)
        if not ok:
            log.info("[%s] Compra omitida por riesgo global: %s", self.name, gerr)
            return None

        position = self.risk.build_position(self.symbol, price, quote_amount)
        self._apply_precision(position)  # ajustar cantidad y precios
        slippage_note = ""

        if self.live:
            try:
                resp = self.client.new_order(
                    symbol=self.symbol,
                    side="BUY",
                    order_type="MARKET",
                    quote_order_qty=quote_amount,
                )
            except Exception:
                self.global_risk.release(quote_amount)  # la orden no se envió
                raise
            log.info("[%s] Orden BUY real enviada: %s", self.name, resp)
            outcome = resolve_order_outcome(self.client, self.symbol, resp)
            if outcome.failed:
                # La orden terminó sin ejecutar nada: no hay posición que abrir.
                self.global_risk.release(quote_amount)
                log.error("[%s] Orden BUY no ejecutada (estado %s): no se abre posición.",
                          self.name, outcome.status)
                self.notifier.send(
                    f"⚠️ [{self.name}] Orden de compra en {self.symbol} no ejecutada "
                    f"(estado {outcome.status})."
                )
                return None
            if outcome.filled:
                fill_price, fill_qty = outcome.avg_price, outcome.executed_qty
                slippage_pct = (fill_price / price - 1) * 100
                # Reconstruir la posición con los datos reales de ejecución:
                # SL/TP se recalculan desde el precio medio real de compra.
                position = self.risk.build_position(self.symbol, fill_price, quote_amount)
                position.quantity = fill_qty
                self._apply_precision(position)
                price = fill_price
                slippage_note = f"\nSlippage: {slippage_pct:+.4f}%"
                log.info("[%s] Fill real BUY: %.8f @ %.8f | slippage=%+.4f%%",
                         self.name, fill_qty, fill_price, slippage_pct)
                if outcome.partial:
                    log.warning("[%s] Compra ejecutada PARCIALMENTE (%s): la posición "
                                "se registra solo con lo realmente comprado.",
                                self.name, outcome.status)
                    slippage_note += "\n⚠️ Ejecución parcial (resto cancelado)"
            else:
                log.warning("[%s] La orden BUY no reporta ejecución (estado '%s'); la "
                            "posición se registra con el precio de la vela (%.2f) como "
                            "estimación.", self.name, outcome.status, price)
        else:
            log.info("[%s] [PAPER] Compra simulada %.8f @ %.2f (SL %.2f / TP %.2f)",
                     self.name, position.quantity, price, position.stop_loss, position.take_profit)
        self.risk.register_open(position)
        self.store.add_position(position, bot=self.name)  # persistir la posición abierta
        self.global_risk.confirm(position, quote_amount)  # reserva -> compromiso real
        if self.live:
            # Colocar el take-profit como orden LIMIT en el exchange, para que
            # se ejecute aunque el bot esté caído. (MEXC spot no admite órdenes
            # stop, así que el stop-loss se sigue vigilando localmente.)
            self._place_tp_order(position)
        self.notifier.send(
            f"🟢 <b>COMPRA</b> [{self.name}] ({self.mode_label})\n"
            f"{self.symbol}\n"
            f"Precio: <b>{price:.2f}</b>\n"
            f"Cantidad: {position.quantity:.8f}\n"
            f"Stop-loss: {position.stop_loss:.2f}\n"
            f"Take-profit: {position.take_profit:.2f}"
            f"{slippage_note}"
        )
        return position

    def _market_sell(self, position: Position, price: float, reason: str) -> None:
        exit_price = price
        slippage_note = ""
        tp_sold_qty = tp_sold_quote = 0.0  # parte ya vendida por la orden TP cancelada
        if self.live:
            # Si hay una orden TP en el exchange, cancelarla primero para
            # liberar el saldo que tiene retenido.
            if position.tp_order_id:
                canceled = self._cancel_tp_order(position)
                if canceled is None:
                    return  # no se pudo cancelar ni aclarar: reintentar en el próximo ciclo
                tp_sold_qty, tp_sold_quote = canceled
                if tp_sold_qty > 0 and position.quantity - tp_sold_qty <= 0:
                    # La orden TP ya lo había vendido todo.
                    self._finalize_close(position, tp_sold_quote / tp_sold_qty,
                                         "take_profit",
                                         "\n(ejecutado por la orden TP del exchange)")
                    return

            # Ajustar la cantidad al saldo libre real y a la precisión del
            # símbolo antes de vender.
            sell_qty = self._sellable_qty(position.quantity - tp_sold_qty)
            if self.symbol_info is not None:
                sell_qty, err = self.symbol_info.check_sell_qty(sell_qty)
                if err:
                    self._register_close_failure(position, err)
                    return
            resp = self.client.new_order(
                symbol=self.symbol,
                side="SELL",
                order_type="MARKET",
                quantity=sell_qty,
            )
            log.info("[%s] Orden SELL real enviada (%s): %s", self.name, reason, resp)
            outcome = resolve_order_outcome(self.client, self.symbol, resp)
            if outcome.failed:
                # No se vendió nada: la posición sigue abierta y se
                # reintentará el cierre en el próximo ciclo.
                log.error("[%s] Orden SELL no ejecutada (estado %s): la posición "
                          "sigue abierta; se reintentará.", self.name, outcome.status)
                self.notifier.send(
                    f"⚠️ [{self.name}] Orden de venta en {self.symbol} no ejecutada "
                    f"(estado {outcome.status}). La posición sigue abierta."
                )
                return
            if outcome.filled:
                slippage_pct = (outcome.avg_price / price - 1) * 100
                slippage_note = f"\nSlippage: {slippage_pct:+.4f}%"
                log.info("[%s] Fill real SELL: @ %.8f | slippage=%+.4f%%",
                         self.name, outcome.avg_price, slippage_pct)
                if outcome.partial:
                    # Solo se vendió una parte: el PnL se calcula sobre lo
                    # realmente vendido; el resto queda sin vender en la cuenta.
                    remainder = position.quantity - tp_sold_qty - outcome.executed_qty
                    position.quantity = tp_sold_qty + outcome.executed_qty
                    log.warning("[%s] Venta PARCIAL (%s): vendidas %.8f, quedan %.8f "
                                "sin vender en la cuenta.", self.name, outcome.status,
                                outcome.executed_qty, remainder)
                    slippage_note += (f"\n⚠️ Venta parcial: {remainder:.8f} "
                                      f"sin vender en la cuenta")
                # Precio de salida: media ponderada de lo vendido por la orden
                # TP (si vendió algo antes de cancelarse) y la venta MARKET.
                total_qty = tp_sold_qty + outcome.executed_qty
                exit_price = (tp_sold_quote
                              + outcome.avg_price * outcome.executed_qty) / total_qty
            else:
                log.warning("[%s] La orden SELL no reporta ejecución (estado '%s'); el "
                            "PnL se calcula con el precio de la vela (%.2f).",
                            self.name, outcome.status, price)
        self._finalize_close(position, exit_price, reason, slippage_note)

    # Nº de intentos de cierre fallidos por cantidad invendible antes de
    # retirar la posición como 'dust' (polvo).
    DUST_CLOSE_AFTER = 3

    @staticmethod
    def _pos_key(position: Position):
        return position.id if position.id is not None else id(position)

    def _register_close_failure(self, position: Position, err: str) -> None:
        """Registra un intento de cierre con cantidad invendible.

        Sin esto, una posición cuya cantidad no alcanza el mínimo vendible
        reintentaría (y notificaría) en cada ciclo para siempre. Tras
        DUST_CLOSE_AFTER intentos consecutivos se retira del seguimiento con
        un cierre administrativo (motivo 'dust', PnL 0); el resto queda como
        polvo en la cuenta."""
        key = self._pos_key(position)
        count = self._close_failures.get(key, 0) + 1
        self._close_failures[key] = count
        log.error("[%s] No se pudo vender %s (intento %d/%d): %s",
                  self.name, self.symbol, count, self.DUST_CLOSE_AFTER, err)
        if count == 1:  # avisar solo la primera vez (evitar spam por ciclo)
            self.notifier.send(f"⚠️ [{self.name}] No se pudo cerrar {self.symbol}: {err}")
        if count < self.DUST_CLOSE_AFTER:
            return
        log.warning("[%s] Posición %s retirada como polvo (dust) tras %d intentos: "
                    "cantidad invendible.", self.name, key, count)
        self.store.close_position(position, position.entry_price, 0.0, "dust",
                                  bot=self.name)
        if position in self.risk.open_positions:
            self.risk.open_positions.remove(position)
        self.global_risk.register_close(position, 0.0)
        self._close_failures.pop(key, None)
        self.notifier.send(
            f"🧹 [{self.name}] Posición en {self.symbol} retirada como polvo (dust): "
            f"cantidad invendible tras {count} intentos. Queda como resto en la cuenta."
        )

    def _finalize_close(self, position: Position, exit_price: float,
                        reason: str, slippage_note: str = "") -> None:
        """Contabiliza y persiste el cierre de una posición ya vendida."""
        self._close_failures.pop(self._pos_key(position), None)
        pnl = self.risk.register_close(position, exit_price)
        self.global_risk.register_close(position, pnl)  # actualizar el riesgo global
        # Persistir el cierre y el nuevo estado diario.
        self.store.close_position(position, exit_price, pnl, reason, bot=self.name)
        self.store.save_daily_state(_today_str(), self.risk.daily_pnl, self.risk.halted, bot=self.name)
        log.info("[%s] [%s] Cierre por %s @ %.2f | PnL=%.4f | PnL diario=%.4f",
                 self.name, "LIVE" if self.live else "PAPER", reason, exit_price, pnl, self.risk.daily_pnl)

        emoji = "✅" if pnl >= 0 else "🔴"
        reasons_es = {
            "stop_loss": "Stop-loss",
            "take_profit": "Take-profit",
            "signal_sell": "Señal de venta",
        }
        self.notifier.send(
            f"{emoji} <b>VENTA</b> [{self.name}] ({self.mode_label})\n"
            f"{self.symbol}\n"
            f"Motivo: {reasons_es.get(reason, reason)}\n"
            f"Precio: <b>{exit_price:.2f}</b>\n"
            f"PnL operación (neto): <b>{pnl:+.4f}</b>\n"
            f"PnL del día: {self.risk.daily_pnl:+.4f}"
            f"{slippage_note}"
        )

        if self.risk.halted:
            log.warning("[%s] Límite de pérdida diaria alcanzado. Deja de abrir posiciones.",
                        self.name)
            self.notifier.send(
                f"⛔ <b>[{self.name}] Límite de pérdida diaria alcanzado</b>\n"
                "El bot deja de abrir nuevas posiciones hasta mañana."
            )

    # ------------------------------------------------------------------
    def _step(self) -> None:
        """Un ciclo: obtener datos, evaluar salidas y luego entradas."""
        raw = self.client.get_klines(self.symbol, self.bot.interval, limit=200)
        df = klines_to_df(raw)
        price = float(df["close"].iloc[-1])  # precio actual (vela en formación)

        # 1) (live) ¿Alguna orden TP del exchange se ejecutó (quizá estando
        #    el bot caído)? Contabilizar esos cierres primero.
        if self.live:
            self._check_exchange_tp()

        # 2) Trailing stop: subir el stop-loss siguiendo al precio actual
        #    (persistiendo el nuevo nivel para que sobreviva reinicios).
        for position in self.risk.open_positions:
            if self.risk.update_trailing(position, price):
                if self.symbol_info is not None:
                    position.stop_loss = self.symbol_info.round_price(position.stop_loss)
                self.store.update_position_stop(position.id, position.stop_loss)
                log.info("[%s] Trailing stop: stop-loss subido a %.2f (precio %.2f).",
                         self.name, position.stop_loss, price)

        # 3) Revisar salidas de posiciones abiertas (SL/TP) con el precio actual.
        for position in list(self.risk.open_positions):
            reason = self.risk.should_close(position, price)
            if reason == "take_profit" and position.tp_order_id:
                continue  # el TP lo ejecuta la orden LIMIT del exchange
            if reason:
                self._market_sell(position, price, reason)

        # 4) Evaluar la estrategia SOLO con velas cerradas: la última vela de
        #    MEXC es la que está en formación y sus señales pueden deshacerse
        #    antes del cierre (así, además, live coincide con el backtest).
        #    Cada vela cerrada se evalúa una única vez para no repetir la
        #    misma señal en cada poll dentro del mismo intervalo.
        closed = df.iloc[:-1]
        if len(closed) == 0:
            return
        candle_id = closed["open_time"].iloc[-1]
        if candle_id == self._last_signal_candle:
            log.info("[%s] Precio=%.2f | posiciones=%d | esperando cierre de vela",
                     self.name, price, len(self.risk.open_positions))
            return
        self._last_signal_candle = candle_id

        signal = self.strategy.generate_signal(closed)
        log.info("[%s] Precio=%.2f | señal=%s | posiciones=%d",
                 self.name, price, signal.value, len(self.risk.open_positions))

        if signal == Signal.BUY and self.risk.can_open():
            self._market_buy(price)
        elif signal == Signal.SELL:
            # Cierre por señal de estrategia (además de SL/TP).
            for position in list(self.risk.open_positions):
                self._market_sell(position, price, "signal_sell")

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Bucle principal. Se detiene con Ctrl+C o cuando `stop_event` se activa."""
        log.info("[%s] Comprobando conectividad con MEXC...", self.name)
        self.client.ping()
        log.info("[%s] Conectado. Iniciando bucle (cada %ds).",
                 self.name, self.bot.poll_seconds)

        def _stopped() -> bool:
            return stop_event is not None and stop_event.is_set()

        current_day = datetime.now(timezone.utc).date()
        try:
            while not _stopped():
                today = datetime.now(timezone.utc).date()
                if today != current_day:
                    self.risk.reset_daily()
                    self.global_risk.reset_daily(today.isoformat())  # una vez por día
                    self.client.sync_time()  # corregir la deriva del reloj a diario
                    self.store.save_daily_state(
                        today.isoformat(), self.risk.daily_pnl, self.risk.halted, bot=self.name
                    )
                    current_day = today
                    log.info("[%s] Nuevo día: contador de pérdidas reiniciado.", self.name)

                try:
                    self._step()
                except Exception as exc:  # noqa: BLE001 - un fallo puntual no debe matar el bot
                    log.error("[%s] Error en el ciclo: %s", self.name, exc)

                # Espera interrumpible: si hay stop_event, reacciona al instante.
                if stop_event is not None:
                    if stop_event.wait(self.bot.poll_seconds):
                        break
                else:
                    time.sleep(self.bot.poll_seconds)
        except KeyboardInterrupt:
            log.info("[%s] Detenido por el usuario. PnL de la sesión: %.4f",
                     self.name, self.risk.daily_pnl)
        finally:
            self.store.close()
            log.info("[%s] Motor detenido.", self.name)
