"""Aplicación Flask del dashboard.

Es de SOLO LECTURA: abre la base de datos SQLite del bot y muestra las
posiciones abiertas, el historial de operaciones y estadísticas. No ejecuta
órdenes ni modifica nada.
"""
from pathlib import Path

from flask import Flask, jsonify, render_template

from ..analytics import compute_metrics, equity_curve
from ..persistence import PositionStore


def create_app(db_path: str = "data/bot.db") -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

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
            return jsonify({
                "summary": store.fetch_summary(),
                "metrics": compute_metrics(all_trades),
                "equity_curve": equity_curve(all_trades),
                "open_positions": store.fetch_open_positions(),
                "trades": store.fetch_trades(limit=50),
                "daily": store.fetch_daily_states(limit=14),
            })
        finally:
            store.close()

    return app
