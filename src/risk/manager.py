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

    def should_close(self, position: Position, current_price: float) -> str | None:
        """Devuelve 'stop_loss', 'take_profit' o None."""
        if current_price <= position.stop_loss:
            return "stop_loss"
        if current_price >= position.take_profit:
            return "take_profit"
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
