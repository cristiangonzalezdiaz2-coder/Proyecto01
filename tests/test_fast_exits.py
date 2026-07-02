"""Pruebas del tick rápido de salidas con feed de precios (WebSocket).

Con feed activo, run() comprueba stop-loss/trailing cada fast_tick segundos
con el último precio del feed, sin esperar al siguiente poll REST. Si el
feed no tiene dato fresco (None), no se hace nada hasta el próximo poll.
"""
import threading
import time

import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError
from src.persistence import PositionStore
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine


def _kline(open_time: int, close: float) -> list:
    return [open_time, close, close, close, close, 1.0, open_time + 59_999, 1.0]


class FakeClient:
    def __init__(self, api_key="", api_secret=""):
        # Velas planas a 40000: el ciclo completo no dispara nada.
        self.klines = [_kline(i, 40000.0) for i in range(5)]

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")

    def get_klines(self, symbol, interval, limit=200):
        return self.klines


class FakeFeed:
    """Feed controlable desde el test (misma interfaz que WebSocketPriceFeed)."""

    def __init__(self):
        self.value = None

    def price(self, symbol):
        return self.value


def _make_engine(tmp_path, monkeypatch, feed) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1m",
                    poll_seconds=9999,  # el poll no interviene en el test
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0))
    cfg = AppConfig(api_key="", api_secret="", trading_mode="paper",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    eng = TradingEngine(cfg, bot, price_feed=feed)
    eng.fast_tick = 0.01
    return eng


def _wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_fast_tick_triggers_stop_between_polls(tmp_path, monkeypatch):
    feed = FakeFeed()
    eng = _make_engine(tmp_path, monkeypatch, feed)
    pos = eng.risk.build_position("BTCUSDT", 40000.0)  # SL 39200
    eng.risk.register_open(pos)
    eng.store.add_position(pos, bot="t")

    stop_event = threading.Event()
    thread = threading.Thread(target=eng.run, args=(stop_event,), daemon=True)
    thread.start()
    try:
        # El primer ciclo completo (velas a 40000) no cierra nada.
        assert _wait_until(lambda: len(eng.risk.open_positions) == 1, timeout=2)
        time.sleep(0.05)
        assert eng.risk.open_positions  # sin precio del feed, sigue abierta

        # El feed publica un precio bajo el stop: cierre en el tick rápido,
        # sin esperar al siguiente poll (que tardaría 9999 s).
        feed.value = 39000.0
        assert _wait_until(lambda: not eng.risk.open_positions)
    finally:
        stop_event.set()
        thread.join(timeout=5)
    assert not thread.is_alive()

    # El cierre quedó registrado con el precio del feed.
    store = PositionStore(str(tmp_path / "t.db"))
    trade = store.fetch_trades()[0]
    store.close()
    assert trade["reason"] == "stop_loss"
    assert trade["exit_price"] == pytest.approx(39000.0)


def test_without_feed_loop_behaves_as_before(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, feed=None)
    eng.bot.poll_seconds = 1
    pos = eng.risk.build_position("BTCUSDT", 40000.0)
    eng.risk.register_open(pos)
    eng.store.add_position(pos, bot="t")

    stop_event = threading.Event()
    thread = threading.Thread(target=eng.run, args=(stop_event,), daemon=True)
    thread.start()
    try:
        # El precio de las velas (40000) no toca SL/TP: la posición sigue.
        time.sleep(0.3)
        assert eng.risk.open_positions
    finally:
        stop_event.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
