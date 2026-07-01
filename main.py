"""Punto de entrada del bot de trading MEXC.

Uso:
    python main.py                 # arranca el bot con config/config.yaml
    python main.py --check         # solo comprueba conexión y credenciales
"""
import argparse
import sys

from src.config import load_config
from src.logger import get_logger
from src.mexc import MexcSpotClient
from src.trading import TradingEngine

log = get_logger("main")


def check_connection(cfg) -> int:
    client = MexcSpotClient(cfg.api_key, cfg.api_secret)
    try:
        client.ping()
        log.info("Conexión con MEXC: OK")
        price = client.get_price(cfg.symbol)
        log.info("Precio actual %s: %.2f", cfg.symbol, price)
        if cfg.api_key and cfg.api_secret:
            usdt = client.get_balance("USDT")
            log.info("Balance USDT (spot): %.4f", usdt)
        else:
            log.warning("Sin credenciales: solo acceso a datos públicos.")
        return 0
    except Exception as exc:  # noqa: BLE001
        log.error("Fallo de comprobación: %s", exc)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot de trading MEXC (spot)")
    parser.add_argument("--check", action="store_true", help="Comprobar conexión y salir")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al YAML de config")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.check:
        return check_connection(cfg)

    if cfg.trading_mode == "live":
        log.warning("=== MODO LIVE: se enviarán órdenes REALES a MEXC ===")

    engine = TradingEngine(cfg)
    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
