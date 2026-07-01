"""Aplicación Flask del dashboard.

Es de SOLO LECTURA: abre la base de datos SQLite del bot y muestra las
posiciones abiertas, el historial de operaciones y estadísticas. No ejecuta
órdenes ni modifica nada.
"""
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

from ..analytics import compute_metrics, equity_curve
from ..config import GlobalRiskConfig
from ..persistence import PositionStore


def create_app(db_path: str = "data/bot.db",
               global_risk: GlobalRiskConfig | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    grc = global_risk or GlobalRiskConfig()

    def _store() -> PositionStore:
        # Una conexión por petición: SQLite en modo WAL lo tolera bien.
        return PositionStore(app.config["DB_PATH"])

    @app.route("/")
    def index():
        db_exists = Path(app.config["DB_PATH"]).exists()
        return render_template("index.html", db_exists=db_exists)

    @app.route("/api/state")
    def api_state():
        """Devuelve todo el estado en JSON para que la página lo refresque."""
        store = _store()
        try:
            all_trades = store.fetch_all_trades()
            open_positions = store.fetch_open_positions()

            # Desglose por bot: métricas independientes de cada uno.
            open_by_bot: dict[str, int] = {}
            for p in open_positions:
                open_by_bot[p.get("bot", "default")] = open_by_bot.get(p.get("bot", "default"), 0) + 1

            trades_by_bot: dict[str, list] = {}
            for t in all_trades:
                trades_by_bot.setdefault(t.get("bot", "default"), []).append(t)

            bot_names = sorted(set(open_by_bot) | set(trades_by_bot))
            bots = []
            for name in bot_names:
                bt = trades_by_bot.get(name, [])
                m = compute_metrics(bt)
                bots.append({
                    "name": name,
                    "open_positions": open_by_bot.get(name, 0),
                    "total_trades": m["total_trades"],
                    "win_rate": m["win_rate"],
                    "total_pnl": m["total_pnl"],
                    "profit_factor": m["profit_factor"],
                    "max_drawdown": m["max_drawdown"],
                })

            # Riesgo global: estado actual (BD) frente a los límites configurados.
            today = datetime.now(timezone.utc).date().isoformat()
            exposure, open_count = store.fetch_open_exposure()
            global_daily_pnl = store.fetch_global_daily_pnl(today)
            global_risk_state = {
                "enabled": grc.enabled,
                "exposure": exposure,
                "max_total_exposure": grc.max_total_exposure,
                "open_count": open_count,
                "max_open_positions": grc.max_open_positions,
                "daily_pnl": global_daily_pnl,
                "max_daily_loss": grc.max_daily_loss,
                "halted": bool(grc.max_daily_loss) and global_daily_pnl <= -abs(grc.max_daily_loss),
            }

            return jsonify({
                "summary": store.fetch_summary(),
                "metrics": compute_metrics(all_trades),
                "equity_curve": equity_curve(all_trades),
                "bots": bots,
                "global_risk": global_risk_state,
                "open_positions": open_positions,
                "trades": store.fetch_trades(limit=50),
                "daily": store.fetch_daily_states(limit=14),
            })
        finally:
            store.close()

    return app
