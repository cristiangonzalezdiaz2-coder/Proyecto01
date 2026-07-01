"""Gestión de riesgo: posiciones, stop-loss, take-profit y límite de pérdida diaria."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import RiskConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Position:
    symbol: str
    entry_price: float
    quantity: float          # cantidad en el activo base
    stop_loss: float
    take_profit: float
    opened_at: str = field(default_factory=_now_iso)
    id: Optional[int] = None  # id en la base de datos (None si aún no persistida)
    # Id de la orden LIMIT de take-profit colocada en el exchange (solo live).
    tp_order_id: Optional[str] = None

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.quantity


class RiskManager:
    """Aplica las reglas de riesgo definidas en la configuración."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self.daily_pnl = 0.0
        self.open_positions: list[Position] = []
        self.halted = False  # True si se alcanzó el límite de pérdida diaria

    # ------------------------------------------------------------------
    def can_open(self) -> bool:
        """¿Se permite abrir una nueva posición?"""
        if self.halted:
            return False
        return len(self.open_positions) < self.config.max_open_positions

    def build_position(self, symbol: str, entry_price: float) -> Position:
        """Crea una posición dimensionada según quote_per_trade y los % de riesgo."""
        quantity = self.config.quote_per_trade / entry_price
        stop_loss = entry_price * (1 - self.config.stop_loss_pct)
        take_profit = entry_price * (1 + self.config.take_profit_pct)
        return Position(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def register_open(self, position: Position) -> None:
        self.open_positions.append(position)

    def update_trailing(self, position: Position, price: float) -> bool:
        """Trailing stop: sube el stop-loss siguiendo al precio.

        Si trailing_stop_pct > 0, el stop se coloca a esa distancia por debajo
        del máximo alcanzado. Solo sube (nunca baja del nivel actual), así que
        con el avance del precio pasa a asegurar beneficios. Devuelve True si
        el stop subió (el llamador debe persistir el cambio)."""
        pct = self.config.trailing_stop_pct
        if not pct:
            return False
        new_stop = price * (1 - pct)
        if new_stop > position.stop_loss:
            position.stop_loss = new_stop
            return True
        return False

    def should_close(self, position: Position, current_price: float) -> str | None:
        """Devuelve 'stop_loss', 'take_profit' o None (para el bot en vivo,
        que evalúa contra el precio actual en cada ciclo)."""
        if current_price <= position.stop_loss:
            return "stop_loss"
        if current_price >= position.take_profit:
            return "take_profit"
        return None

    def check_candle_exit(self, position: Position, open_: float,
                          high: float, low: float) -> tuple[str, float] | None:
        """Evalúa SL/TP contra el RANGO de una vela (para backtesting).

        Mirar solo el cierre ignora los stops y TPs tocados dentro de la vela
        e infla los resultados. Devuelve (motivo, precio_de_salida) o None.

        Convenciones (conservadoras):
          - Si la vela toca el stop y el take-profit, gana el stop.
          - Gap bajista: si la vela abre por debajo del stop, se sale al precio
            de apertura (peor que el stop), como haría una orden de mercado.
          - Gap alcista: si abre por encima del TP, la venta LIMIT se ejecuta
            a la apertura (mejor que el TP)."""
        if low <= position.stop_loss:
            return "stop_loss", min(open_, position.stop_loss)
        if high >= position.take_profit:
            return "take_profit", max(open_, position.take_profit)
        return None

    def trade_fees(self, position: Position, exit_price: float) -> float:
        """Comisiones estimadas de la operación completa (compra + venta)."""
        return (position.entry_price + exit_price) * position.quantity * self.config.fee_pct

    def register_close(self, position: Position, exit_price: float) -> float:
        """Cierra una posición, actualiza el PnL diario y aplica el límite.

        El PnL devuelto es NETO: descuenta las comisiones de compra y venta."""
        pnl = position.unrealized_pnl(exit_price) - self.trade_fees(position, exit_price)
        self.daily_pnl += pnl
        if position in self.open_positions:
            self.open_positions.remove(position)
        if self.daily_pnl <= -abs(self.config.max_daily_loss):
            self.halted = True
        return pnl

    def reset_daily(self) -> None:
        """Reinicia el contador diario (llamar al cambiar de día)."""
        self.daily_pnl = 0.0
        self.halted = False
