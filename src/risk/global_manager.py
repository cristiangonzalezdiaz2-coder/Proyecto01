"""Gestor de riesgo GLOBAL, compartido por todos los bots.

Impone límites combinados (a través de todos los bots que corren en paralelo):
  - Exposición total: suma del capital comprometido en posiciones abiertas.
  - Pérdida diaria combinada: si se alcanza, se bloquean TODAS las aperturas.
  - Número total de posiciones abiertas simultáneas.

Es thread-safe (los bots corren en hilos distintos) mediante un lock. Un límite
de 0 significa "sin límite" en esa dimensión.
"""
import threading

from ..config import GlobalRiskConfig
from ..logger import get_logger
from .manager import Position

log = get_logger("global_risk")


class GlobalRiskManager:
    def __init__(self, config: GlobalRiskConfig):
        self.config = config
        self._lock = threading.Lock()
        self.exposure = 0.0
        self.open_count = 0
        self.daily_pnl = 0.0
        self.halted = False
        self._day: str | None = None  # día en curso (para reseteo idempotente)
        # Capital comprometido por posición (clave: id de la posición en la BD).
        self._committed: dict[int, float] = {}

    @staticmethod
    def _committed_amount(position: Position) -> float:
        return position.entry_price * position.quantity

    # ------------------------------------------------------------------
    def initialize(self, open_positions: list[Position], daily_pnl: float) -> None:
        """Reconstruye el estado global desde la BD al arrancar."""
        with self._lock:
            self.exposure = 0.0
            self.open_count = 0
            self._committed.clear()
            for pos in open_positions:
                amount = self._committed_amount(pos)
                if pos.id is not None:
                    self._committed[pos.id] = amount
                self.exposure += amount
                self.open_count += 1
            self.daily_pnl = daily_pnl
            self._update_halt_locked()
            if self.config.enabled:
                log.info("Riesgo global inicializado | exposición=%.2f | abiertas=%d | "
                         "PnL diario=%.4f | bloqueado=%s",
                         self.exposure, self.open_count, self.daily_pnl, self.halted)

    # ------------------------------------------------------------------
    def can_open(self, quote_amount: float) -> tuple[bool, str | None]:
        """¿Permiten los límites globales abrir una posición de `quote_amount`?"""
        if not self.config.enabled:
            return True, None
        with self._lock:
            if self.halted:
                return False, "límite de pérdida diaria global alcanzado"
            c = self.config
            if c.max_open_positions and self.open_count >= c.max_open_positions:
                return False, (f"máximo global de posiciones abiertas "
                               f"({c.max_open_positions}) alcanzado")
            if c.max_total_exposure and (self.exposure + quote_amount) > c.max_total_exposure:
                return False, (f"exposición {self.exposure + quote_amount:.2f} superaría "
                               f"el máximo global {c.max_total_exposure:.2f}")
            return True, None

    def register_open(self, position: Position) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            amount = self._committed_amount(position)
            if position.id is not None:
                self._committed[position.id] = amount
            self.exposure += amount
            self.open_count += 1

    def register_close(self, position: Position, pnl: float) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            amount = self._committed.pop(position.id, self._committed_amount(position)) \
                if position.id is not None else self._committed_amount(position)
            self.exposure = max(0.0, self.exposure - amount)
            self.open_count = max(0, self.open_count - 1)
            self.daily_pnl += pnl
            was_halted = self.halted
            self._update_halt_locked()
            if self.halted and not was_halted:
                log.warning("Límite de pérdida diaria GLOBAL alcanzado (%.4f). "
                            "Todos los bots dejan de abrir posiciones.", self.daily_pnl)

    def reset_daily(self, day: str | None = None) -> None:
        """Reinicia el PnL diario global. Si se pasa `day`, solo reinicia una vez
        por día (idempotente cuando varios bots detectan el cambio de día)."""
        with self._lock:
            if day is not None and day == self._day:
                return
            self.daily_pnl = 0.0
            self.halted = False
            self._day = day

    def _update_halt_locked(self) -> None:
        c = self.config
        self.halted = bool(c.max_daily_loss) and self.daily_pnl <= -abs(c.max_daily_loss)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "exposure": self.exposure,
                "open_count": self.open_count,
                "daily_pnl": self.daily_pnl,
                "halted": self.halted,
            }
