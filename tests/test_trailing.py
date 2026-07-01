"""Pruebas del trailing stop.

Con trailing_stop_pct > 0, el stop-loss sube siguiendo al precio (a esa
distancia del máximo alcanzado) y nunca baja: al avanzar el precio pasa de
limitar pérdidas a asegurar beneficios. En el backtest, el máximo de una vela
sube el stop con efecto desde la vela SIGUIENTE (convención conservadora).
"""
import pandas as pd
import pytest
import yaml

from backtest import simulate
from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig, load_config
from src.mexc import MexcError
from src.risk import RiskManager
from src.strategies import Signal
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine


# ------------------------- RiskManager.update_trailing -------------------------
def test_disabled_by_default():
    rm = RiskManager(RiskConfig(fee_pct=0.0))
    pos = rm.build_position("BTCUSDT", 100.0)
    assert rm.update_trailing(pos, 150.0) is False
    assert pos.stop_loss == pytest.approx(98.0)  # intacto


def test_stop_rises_with_price():
    rm = RiskManager(RiskConfig(fee_pct=0.0, trailing_stop_pct=0.015))
    pos = rm.build_position("BTCUSDT", 100.0)  # SL inicial 98
    assert rm.update_trailing(pos, 105.0) is True
    assert pos.stop_loss == pytest.approx(105.0 * 0.985)


def test_stop_never_goes_down():
    rm = RiskManager(RiskConfig(fee_pct=0.0, trailing_stop_pct=0.015))
    pos = rm.build_position("BTCUSDT", 100.0)
    rm.update_trailing(pos, 105.0)
    trailed = pos.stop_loss
    assert rm.update_trailing(pos, 104.0) is False  # el precio retrocede
    assert pos.stop_loss == pytest.approx(trailed)


def test_stop_never_below_initial():
    rm = RiskManager(RiskConfig(fee_pct=0.0, trailing_stop_pct=0.015))
    pos = rm.build_position("BTCUSDT", 100.0)  # SL inicial 98
    assert rm.update_trailing(pos, 99.0) is False  # 99*0.985 = 97.5 < 98
    assert pos.stop_loss == pytest.approx(98.0)


# ------------------------- Backtest -------------------------
class BuyOnce:
    name = "buy_once"

    def __init__(self, buy_at: int):
        self.buy_at = buy_at

    def generate_signal(self, candles):
        return Signal.BUY if len(candles) - 1 == self.buy_at else Signal.HOLD


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_simulate_trailing_locks_in_profit():
    # Compra @100 (SL 98 / TP 104). El precio sube a 103 (sin tocar el TP) y
    # luego cae: con trailing del 2% el stop subió a 100.94 y la caída cierra
    # CON beneficio; con stop fijo no habría salida.
    df = _df([
        [100, 100, 100, 100],
        [100, 100, 100, 100],
        [100, 101, 99, 100],
        [100, 103, 100, 102],
        [102, 102.5, 100, 101],
    ])
    res = simulate(df, "BTCUSDT", BuyOnce(buy_at=2), fee_pct=0.0, trailing_pct=0.02)
    assert res["trades"] == 1 and res["wins"] == 1
    assert res["pnl"] == pytest.approx((103 * 0.98 - 100.0) * 0.2)

    sin_trailing = simulate(df, "BTCUSDT", BuyOnce(buy_at=2), fee_pct=0.0)
    assert sin_trailing["trades"] == 0  # el stop fijo (98) nunca se toca


def test_simulate_trailing_effective_from_next_candle():
    # El high de la vela 3 (103) NO puede disparar el stop trailed en esa
    # misma vela aunque su low (100.5) quede bajo 100.94.
    df = _df([
        [100, 100, 100, 100],
        [100, 100, 100, 100],
        [100, 101, 99, 100],
        [100, 103, 100.5, 102],
    ])
    res = simulate(df, "BTCUSDT", BuyOnce(buy_at=2), fee_pct=0.0, trailing_pct=0.02)
    assert res["trades"] == 0  # sigue abierta: el trailing aplica en la siguiente


# ------------------------- Motor (paper) -------------------------
def _kline(open_time: int, close: float) -> list:
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


def _make_engine(tmp_path, monkeypatch) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1m", poll_seconds=1,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0,
                                    trailing_stop_pct=0.015))
    cfg = AppConfig(api_key="", api_secret="", trading_mode="paper",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def test_engine_trails_and_persists_stop(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = eng.risk.build_position("BTCUSDT", 40000.0)  # SL 39200 / TP 41600
    eng.risk.register_open(pos)
    eng.store.add_position(pos, bot="t")

    eng.client.klines = [_kline(0, 40000), _kline(1, 40500), _kline(2, 41000)]
    eng._step()  # precio actual 41000 -> stop sube a 41000*0.985 = 40385

    assert pos.stop_loss == pytest.approx(40385.0)
    assert eng.store.load_open_positions("t")[0].stop_loss == pytest.approx(40385.0)
    assert eng.risk.open_positions == [pos]  # sigue abierta

    # El precio cae bajo el stop trailed: cierre CON beneficio.
    eng.client.klines.append(_kline(3, 40200))
    eng._step()

    assert not eng.risk.open_positions
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "stop_loss"
    assert trade["pnl"] == pytest.approx((40200.0 - 40000.0) * (20.0 / 40000.0))


# ------------------------- Config -------------------------
def test_trailing_loaded_from_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "symbol": "BTCUSDT", "strategy": {"name": "rsi"},
        "risk": {"trailing_stop_pct": 0.015},
    }), encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.bots[0].risk.trailing_stop_pct == 0.015
    assert RiskConfig().trailing_stop_pct == 0.0  # desactivado por defecto
