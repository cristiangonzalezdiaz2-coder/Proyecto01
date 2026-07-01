"""Almacenamiento en SQLite de posiciones abiertas, historial de operaciones
y estado diario (PnL y bloqueo por pérdida máxima).

Permite que el bot se reinicie sin perder sus posiciones abiertas ni el
progreso del límite de pérdida diaria. Cada fila lleva una etiqueta `bot` para
que varios bots (símbolos/estrategias) compartan la misma base de datos sin
mezclarse. Usa solo la biblioteca estándar.
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
        self._migrate()
        log.info("Persistencia activa en %s", db_path)

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bot         TEXT    NOT NULL DEFAULT 'default',
                symbol      TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                quantity    REAL    NOT NULL,
                stop_loss   REAL    NOT NULL,
                take_profit REAL    NOT NULL,
                opened_at   TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'open',
                tp_order_id TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bot         TEXT    NOT NULL DEFAULT 'default',
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
                bot       TEXT    NOT NULL DEFAULT 'default',
                day       TEXT    NOT NULL,
                daily_pnl REAL    NOT NULL,
                halted    INTEGER NOT NULL,
                PRIMARY KEY (bot, day)
            );
            """
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """Actualiza bases de datos antiguas (sin columna `bot`)."""
        for table in ("positions", "trades"):
            if "bot" not in self._columns(table):
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN bot TEXT NOT NULL DEFAULT 'default'"
                )
                log.info("Migración: columna 'bot' añadida a %s.", table)

        if "tp_order_id" not in self._columns("positions"):
            self._conn.execute("ALTER TABLE positions ADD COLUMN tp_order_id TEXT")
            log.info("Migración: columna 'tp_order_id' añadida a positions.")

        if "bot" not in self._columns("daily_state"):
            # Reconstruir daily_state con clave compuesta (bot, day).
            self._conn.executescript(
                """
                ALTER TABLE daily_state RENAME TO daily_state_old;
                CREATE TABLE daily_state (
                    bot       TEXT    NOT NULL DEFAULT 'default',
                    day       TEXT    NOT NULL,
                    daily_pnl REAL    NOT NULL,
                    halted    INTEGER NOT NULL,
                    PRIMARY KEY (bot, day)
                );
                INSERT INTO daily_state (bot, day, daily_pnl, halted)
                    SELECT 'default', day, daily_pnl, halted FROM daily_state_old;
                DROP TABLE daily_state_old;
                """
            )
            log.info("Migración: daily_state ahora es por bot.")
        self._conn.commit()

    # ------------------------- Posiciones -------------------------
    def add_position(self, position: Position, bot: str = "default") -> int:
        """Inserta una posición abierta y le asigna su id de base de datos."""
        cur = self._conn.execute(
            """INSERT INTO positions
               (bot, symbol, entry_price, quantity, stop_loss, take_profit,
                opened_at, status, tp_order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (bot, position.symbol, position.entry_price, position.quantity,
             position.stop_loss, position.take_profit, position.opened_at,
             position.tp_order_id),
        )
        self._conn.commit()
        position.id = cur.lastrowid
        return position.id

    def load_open_positions(self, bot: str | None = None) -> list[Position]:
        """Posiciones abiertas de una sesión anterior. Si `bot` es None, todas."""
        if bot is None:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status = 'open' AND bot = ? ORDER BY id",
                (bot,),
            ).fetchall()
        positions = [
            Position(
                symbol=r["symbol"], entry_price=r["entry_price"], quantity=r["quantity"],
                stop_loss=r["stop_loss"], take_profit=r["take_profit"],
                opened_at=r["opened_at"], id=r["id"], tp_order_id=r["tp_order_id"],
            )
            for r in rows
        ]
        if positions:
            log.info("Recuperadas %d posición(es) abierta(s)%s.",
                     len(positions), f" del bot {bot}" if bot else "")
        return positions

    def set_tp_order(self, position_id: int | None, order_id: str | None) -> None:
        """Guarda (o borra) el id de la orden TP del exchange de una posición."""
        if position_id is None:
            return
        self._conn.execute(
            "UPDATE positions SET tp_order_id = ? WHERE id = ?", (order_id, position_id)
        )
        self._conn.commit()

    def update_position_stop(self, position_id: int | None, stop_loss: float) -> None:
        """Actualiza el stop-loss de una posición (trailing stop)."""
        if position_id is None:
            return
        self._conn.execute(
            "UPDATE positions SET stop_loss = ? WHERE id = ?", (stop_loss, position_id)
        )
        self._conn.commit()

    def update_position_quantity(self, position_id: int | None, quantity: float) -> None:
        """Ajusta la cantidad de una posición (p. ej. tras reconciliar balances)."""
        if position_id is None:
            return
        self._conn.execute(
            "UPDATE positions SET quantity = ? WHERE id = ?", (quantity, position_id)
        )
        self._conn.commit()

    def close_position(self, position: Position, exit_price: float,
                       pnl: float, reason: str, bot: str = "default") -> None:
        """Marca la posición como cerrada y guarda la operación en el historial."""
        closed_at = _now_iso()
        if position.id is not None:
            self._conn.execute(
                "UPDATE positions SET status = 'closed' WHERE id = ?", (position.id,)
            )
        self._conn.execute(
            """INSERT INTO trades
               (bot, position_id, symbol, entry_price, exit_price, quantity, pnl, reason, opened_at, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bot, position.id, position.symbol, position.entry_price, exit_price,
             position.quantity, pnl, reason, position.opened_at, closed_at),
        )
        self._conn.commit()

    # ------------------------- Estado diario -------------------------
    def save_daily_state(self, day: str, daily_pnl: float, halted: bool,
                         bot: str = "default") -> None:
        self._conn.execute(
            """INSERT INTO daily_state (bot, day, daily_pnl, halted) VALUES (?, ?, ?, ?)
               ON CONFLICT(bot, day) DO UPDATE SET daily_pnl = excluded.daily_pnl,
                                                   halted    = excluded.halted""",
            (bot, day, daily_pnl, 1 if halted else 0),
        )
        self._conn.commit()

    def load_daily_state(self, day: str, bot: str = "default") -> tuple[float, bool] | None:
        row = self._conn.execute(
            "SELECT daily_pnl, halted FROM daily_state WHERE bot = ? AND day = ?",
            (bot, day),
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
            "SELECT * FROM daily_state ORDER BY day DESC, bot ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_global_daily_pnl(self, day: str) -> float:
        """Suma del PnL diario de todos los bots para un día (riesgo global)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(daily_pnl), 0) AS s FROM daily_state WHERE day = ?", (day,)
        ).fetchone()
        return float(row["s"] or 0.0)

    def fetch_open_exposure(self) -> tuple[float, int]:
        """Exposición total (suma de entry_price*quantity) y nº de posiciones abiertas."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(entry_price * quantity), 0) AS exp, COUNT(*) AS n "
            "FROM positions WHERE status = 'open'"
        ).fetchone()
        return float(row["exp"] or 0.0), int(row["n"] or 0)

    def fetch_bot_realized_pnl(self, bot: str) -> float:
        """PnL realizado acumulado de un bot (todas sus operaciones cerradas)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS s FROM trades WHERE bot = ?", (bot,)
        ).fetchone()
        return float(row["s"] or 0.0)

    def fetch_bots(self) -> list[str]:
        """Nombres de bots presentes en el historial o con posiciones abiertas."""
        rows = self._conn.execute(
            "SELECT DISTINCT bot FROM trades "
            "UNION SELECT DISTINCT bot FROM positions ORDER BY bot"
        ).fetchall()
        return [r["bot"] for r in rows]

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
