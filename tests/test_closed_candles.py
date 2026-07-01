"""Pruebas de que la estrategia solo ve velas CERRADAS.

La última vela de get_klines es la que está en formación: sus señales pueden
aparecer y deshacerse antes del cierre, así que el motor la excluye al evaluar
la estrategia y evalúa cada vela cerrada una única vez. El precio actual (de
la vela en formación) sí se usa para SL/TP y como precio de ejecución.
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError
from src.strategies import Signal
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine


def _kline(open_time: int, close: float) -> list:
    # [open_time, open, high, low, close, volume, close_time, quote_vol]
    return [open_time, close, close, close, close, 1.0, open_time + 59_999, 1.0]


class FakeClient:
    def __init__(self, api_key="", api_secret=""):
        self.klines: list[list] = []

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")

    def get_klines(self, symbol, interval, limit=200):
        return self.klines


class SpyStrategy:
    """Estrategia espía: registra los DataFrames que recibe y devuelve
    las señales programadas en orden."""
    name = "spy"

    def __init__(self, signals):
        self.signals = list(signals)
        self.seen = []

    def generate_signal(self, candles):
        self.seen.append(candles)
        return self.signals.pop(0) if self.signals else Signal.HOLD


def _make_engine(tmp_path, monkeypatch, signals, max_open=3) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(
        name="t", symbol="BTCUSDT", interval="1m", poll_seconds=1,
        strategy=StrategyConfig(),
        risk=RiskConfig(quote_per_trade=20.0, max_open_positions=max_open),
    )
    cfg = AppConfig(api_key="", api_secret="", trading_mode="paper",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    eng = TradingEngine(cfg, bot)
    eng.strategy = SpyStrategy(signals)
    return eng


def test_strategy_only_sees_closed_candles(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, signals=[Signal.BUY])
    # La vela open_time=3 está en formación (close=110); las cerradas son 0..2.
    eng.client.klines = [_kline(0, 100), _kline(1, 100), _kline(2, 100), _kline(3, 110)]
    eng._step()

    assert len(eng.strategy.seen) == 1
    seen = eng.strategy.seen[0]
    assert seen["open_time"].iloc[-1] == 2  # la vela en formación no se incluye
    # La compra sí se ejecuta al precio ACTUAL (vela en formación).
    assert len(eng.risk.open_positions) == 1
    assert eng.risk.open_positions[0].entry_price == pytest.approx(110.0)


def test_same_closed_candle_not_reevaluated(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, signals=[Signal.BUY, Signal.BUY])
    eng.client.klines = [_kline(0, 100), _kline(1, 100), _kline(2, 105)]
    eng._step()
    eng._step()  # mismo estado de velas: no debe reevaluar ni recomprar

    assert len(eng.strategy.seen) == 1
    assert len(eng.risk.open_positions) == 1  # max_open=3: habría podido abrir otra


def test_new_closed_candle_is_evaluated(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, signals=[Signal.BUY, Signal.BUY])
    eng.client.klines = [_kline(0, 100), _kline(1, 100), _kline(2, 105)]
    eng._step()
    # Cierra la vela 2 y aparece una nueva en formación (open_time=3).
    eng.client.klines = eng.client.klines + [_kline(3, 106)]
    eng._step()

    assert len(eng.strategy.seen) == 2
    assert eng.strategy.seen[1]["open_time"].iloc[-1] == 2
    assert len(eng.risk.open_positions) == 2


def test_exits_still_checked_every_cycle(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, signals=[Signal.BUY])
    eng.client.klines = [_kline(0, 100), _kline(1, 100), _kline(2, 100), _kline(3, 100)]
    eng._step()
    assert len(eng.risk.open_positions) == 1

    # Sin vela cerrada nueva, pero el precio actual cae bajo el stop-loss (2%):
    # la salida debe ejecutarse aunque la estrategia no se reevalúe.
    eng.client.klines = [_kline(0, 100), _kline(1, 100), _kline(2, 100), _kline(3, 97)]
    eng._step()
    assert len(eng.risk.open_positions) == 0
    assert len(eng.strategy.seen) == 1
