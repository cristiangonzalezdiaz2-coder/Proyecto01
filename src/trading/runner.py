"""Ejecuta varios bots en paralelo, cada uno en su propio hilo.

Cada bot es un TradingEngine independiente (su par, su estrategia, su riesgo)
que comparte la base de datos etiquetando sus filas con su nombre. Un único
evento de parada apaga todos los hilos de forma ordenada con Ctrl+C.
"""
import threading
import time
from datetime import datetime, timezone

from ..config import AppConfig
from ..logger import get_logger
from ..persistence import PositionStore
from ..risk import GlobalRiskManager
from .engine import TradingEngine

log = get_logger("runner")


def build_price_feed(config: AppConfig):
    """Crea y arranca el feed de precios WebSocket si está habilitado.

    Devuelve None si está deshabilitado o si falta websocket-client; en ese
    caso los motores funcionan solo con REST, como siempre."""
    if not config.use_websocket:
        return None
    from ..mexc.pricefeed import WebSocketPriceFeed

    feed = WebSocketPriceFeed([b.symbol for b in config.bots],
                              url=config.websocket_url or None)
    if not feed.start():
        return None
    log.info("Feed WebSocket activado: stop-loss/trailing en tiempo casi real.")
    return feed


def build_global_risk(config: AppConfig) -> GlobalRiskManager:
    """Crea el gestor de riesgo global e inicializa su estado desde la BD."""
    manager = GlobalRiskManager(config.global_risk)
    store = PositionStore(config.db_path)
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        open_positions = store.load_open_positions(None)  # de todos los bots
        daily_pnl = store.fetch_global_daily_pnl(today)
        manager.initialize(open_positions, daily_pnl)
    finally:
        store.close()
    if config.global_risk.enabled:
        c = config.global_risk
        log.info("Riesgo global ACTIVO | exposición máx=%s | pérdida diaria máx=%s | "
                 "posiciones máx=%s",
                 c.max_total_exposure or "∞", c.max_daily_loss or "∞",
                 c.max_open_positions or "∞")
    return manager


class MultiRunner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.global_risk = build_global_risk(config)
        self.price_feed = build_price_feed(config)  # compartido entre bots

    def _run_bot(self, bot) -> None:
        """Crea y ejecuta un motor; si el arranque falla, no tumba a los demás."""
        try:
            engine = TradingEngine(self.config, bot, global_risk=self.global_risk,
                                   price_feed=self.price_feed)
            engine.run(stop_event=self.stop_event)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] El bot terminó por un error: %s", bot.name, exc)

    def run(self) -> None:
        bots = self.config.bots
        log.info("Lanzando %d bot(s): %s",
                 len(bots), ", ".join(b.name for b in bots))

        for bot in bots:
            t = threading.Thread(target=self._run_bot, args=(bot,),
                                 name=f"bot-{bot.name}", daemon=True)
            t.start()
            self.threads.append(t)

        # El hilo principal espera hasta Ctrl+C.
        try:
            while any(t.is_alive() for t in self.threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("Deteniendo todos los bots...")
            self.stop_event.set()
            for t in self.threads:
                t.join(timeout=10)
            log.info("Todos los bots detenidos.")
        finally:
            if self.price_feed is not None:
                self.price_feed.stop()
