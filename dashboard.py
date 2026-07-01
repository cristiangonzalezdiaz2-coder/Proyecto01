"""Punto de entrada del dashboard web (solo lectura).

Uso:
    python dashboard.py                 # http://127.0.0.1:8000
    python dashboard.py --port 8080
    python dashboard.py --host 0.0.0.0  # accesible en tu red local

Lee la base de datos configurada en config/config.yaml (db_path).
"""
import argparse

from src.config import load_config
from src.dashboard import create_app
from src.logger import get_logger

log = get_logger("dashboard")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard web del bot MEXC")
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    parser.add_argument("--port", type=int, default=8000, help="Puerto")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al YAML de config")
    parser.add_argument("--db", default=None, help="Sobrescribir la ruta de la base de datos")
    args = parser.parse_args()

    db_path = args.db
    if db_path is None:
        try:
            db_path = load_config(args.config).db_path
        except FileNotFoundError:
            db_path = "data/bot.db"
            log.warning("No hay config.yaml; usando %s por defecto.", db_path)

    app = create_app(db_path)
    log.info("Dashboard en http://%s:%d (BD: %s)", args.host, args.port, db_path)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
