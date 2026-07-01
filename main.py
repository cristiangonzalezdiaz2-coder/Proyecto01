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
from src.trading import MultiRunner, TradingEngine

log = get_logger("main")


def check_connection(cfg) -> int:
    client = MexcSpotClient(cfg.api_key, cfg.api_secret)
    try:
        client.ping()
        log.info("Conexión con MEXC: OK")

        # Comprobar cada bot configurado (par + precisión + mínimo).
        for bot in cfg.bots:
            price = client.get_price(bot.symbol)
            info = client.get_symbol_info(bot.symbol)
            log.info("[%s] %s @ %.2f | cantidad: %d dec | precio: %d dec | "
                     "mín. mercado: %s %s | trading: %s | estrategia: %s",
                     bot.name, bot.symbol, price, info.base_precision, info.quote_precision,
                     info.min_quote_amount_market, info.quote_asset,
                     "permitido" if info.trading_allowed else "NO permitido",
                     bot.strategy.name)
            if bot.risk.quote_per_trade < info.min_quote_amount_market:
                log.warning("[%s] quote_per_trade (%.2f) < mínimo de mercado (%.2f %s): "
                            "las compras serían rechazadas.", bot.name,
                            bot.risk.quote_per_trade, info.min_quote_amount_market, info.quote_asset)

        if cfg.api_key and cfg.api_secret:
            usdt = client.get_balance("USDT")
            log.info("Balance USDT (spot): %.4f", usdt)
        else:
            log.warning("Sin credenciales: solo acceso a datos públicos.")
        return 0
    except Exception as exc:  # noqa: BLE001
        log.error("Fallo de comprobación: %s", exc)
        return 1


def test_telegram(cfg) -> int:
    from src.notifications import TelegramNotifier

    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    if not notifier.enabled:
        log.error("Telegram no configurado. Rellena TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
        return 1
    ok = notifier.send("✅ Prueba de notificación del bot MEXC. ¡Funciona!")
    if ok:
        log.info("Mensaje de prueba enviado correctamente.")
        return 0
    log.error("No se pudo enviar el mensaje. Revisa el token y el chat_id.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot de trading MEXC (spot)")
    parser.add_argument("--check", action="store_true", help="Comprobar conexión y salir")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Enviar un mensaje de prueba a Telegram y salir")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al YAML de config")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.test_telegram:
        return test_telegram(cfg)

    if args.check:
        return check_connection(cfg)

    if cfg.trading_mode == "live":
        log.warning("=== MODO LIVE: se enviarán órdenes REALES a MEXC ===")

    if len(cfg.bots) == 1:
        # Un solo bot: en el hilo principal (Ctrl+C directo).
        TradingEngine(cfg, cfg.bots[0]).run()
    else:
        # Varios bots en paralelo, cada uno en su hilo.
        MultiRunner(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
