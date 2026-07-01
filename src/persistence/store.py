"""Almacenamiento en SQLite de posiciones abiertas, historial de operaciones
y estado diario (PnL y bloqueo por pérdida máxima).

Permite que el bot se reinicie sin perder sus posiciones abiertas ni el
progreso del límite de pérdida diaria. Usa solo la biblioteca estándar.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..logger import get_logger
from ..risk import Position

log = get_logger("store")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PositionStore:
    def __init__(self, db_path: str = "data/bot.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False por si se usa desde hilos distintos.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL permite que el dashboard lea mientras el bot escribe sin bloqueos.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        log.info("Persistencia activa en %s", db_path)

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                quantity    REAL    NOT NULL,
                stop_loss   REAL    NOT NULL,
                take_profit REAL    NOT NULL,
                opened_at   TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                symbol      TEXT,
                entry_price REAL,
                exit_price  REAL,
                quantity    REAL,
                pnl         REAL,
                reason      TEXT,
                opened_at   TEXT,
                closed_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_state (
                day       TEXT    PRIMARY KEY,
                daily_pnl REAL    NOT NULL,
                halted    INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    # ------------------------- Posiciones -------------------------
    def add_position(self, position: Position) -> int:
        """Inserta una posición abierta y le asigna su id de base de datos."""
        cur = self._conn.execute(
            """INSERT INTO positions
               (symbol, entry_price, quantity, stop_loss, take_profit, opened_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            (position.symbol, position.entry_price, position.quantity,
             position.stop_loss, position.take_profit, position.opened_at),
        )
        self._conn.commit()
        position.id = cur.lastrowid
        return position.id

    def load_open_positions(self) -> list[Position]:
        """Devuelve las posiciones que quedaron abiertas de una sesión anterior."""
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY id"
        ).fetchall()
        positions = []
        for r in rows:
            positions.append(Position(
                symbol=r["symbol"],
                entry_price=r["entry_price"],
                quantity=r["quantity"],
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                opened_at=r["opened_at"],
                id=r["id"],
            ))
        if positions:
            log.info("Recuperadas %d posición(es) abierta(s) de la sesión anterior.",
                     len(positions))
        return positions

    def close_position(self, position: Position, exit_price: float,
                       pnl: float, reason: str) -> None:
        """Marca la posición como cerrada y guarda la operación en el historial."""
        closed_at = _now_iso()
        if position.id is not None:
            self._conn.execute(
                "UPDATE positions SET status = 'closed' WHERE id = ?", (position.id,)
            )
        self._conn.execute(
            """INSERT INTO trades
               (position_id, symbol, entry_price, exit_price, quantity, pnl, reason, opened_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (position.id, position.symbol, position.entry_price, exit_price,
             position.quantity, pnl, reason, position.opened_at, closed_at),
        )
        self._conn.commit()

    # ------------------------- Estado diario -------------------------
    def save_daily_state(self, day: str, daily_pnl: float, halted: bool) -> None:
        self._conn.execute(
            """INSERT INTO daily_state (day, daily_pnl, halted) VALUES (?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET daily_pnl = excluded.daily_pnl,
                                              halted    = excluded.halted""",
            (day, daily_pnl, 1 if halted else 0),
        )
        self._conn.commit()

    def load_daily_state(self, day: str) -> tuple[float, bool] | None:
        row = self._conn.execute(
            "SELECT daily_pnl, halted FROM daily_state WHERE day = ?", (day,)
        ).fetchone()
        if row is None:
            return None
        return float(row["daily_pnl"]), bool(row["halted"])

    # ------------------------- Lectura (dashboard) -------------------------
    def fetch_open_positions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_trades(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_all_trades(self) -> list[dict]:
        """Todas las operaciones cerradas en orden cronológico ascendente."""
        rows = self._conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]

    def fetch_daily_states(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM daily_state ORDER BY day DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_summary(self) -> dict:
        """Métricas globales calculadas sobre el historial de operaciones."""
        row = self._conn.execute(
            """SELECT COUNT(*)                        AS total,
                      COALESCE(SUM(pnl), 0)           AS total_pnl,
                      COALESCE(SUM(pnl > 0), 0)       AS wins,
                      COALESCE(AVG(pnl), 0)           AS avg_pnl
               FROM trades"""
        ).fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": (wins / total * 100) if total else 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "open_positions": len(self.fetch_open_positions()),
        }

    def close(self) -> None:
        self._conn.close()
