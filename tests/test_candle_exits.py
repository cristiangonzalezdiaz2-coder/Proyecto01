"""Pruebas de las salidas intra-vela del backtest (high/low, no solo cierre).

Evaluar SL/TP solo contra el precio de cierre ignora los stops tocados dentro
de la vela e infla los resultados del backtest. check_candle_exit usa el rango
completo con convenciones conservadoras: si la vela toca stop y TP, gana el
stop; los gaps se ejecutan al precio de apertura cuando es peor (stop) o mejor
(TP) que el nivel.
"""
import pandas as pd
import pytest

from backtest import simulate
from src.config import RiskConfig
from src.risk import RiskManager
from src.strategies import Signal


def _pos(rm):
    # Entrada a 100 con los defaults: SL = 98 (2%), TP = 104 (4%).
    return rm.build_position("BTCUSDT", 100.0)


def _rm():
    return RiskManager(RiskConfig(fee_pct=0.0))


# ------------------------- check_candle_exit -------------------------
def test_stop_triggered_by_low_even_if_close_recovers():
    rm = _rm()
    exit_ = rm.check_candle_exit(_pos(rm), open_=100.0, high=101.0, low=97.5)
    assert exit_ == ("stop_loss", 98.0)


def test_gap_down_exits_at_open_not_at_stop():
    rm = _rm()
    exit_ = rm.check_candle_exit(_pos(rm), open_=95.0, high=96.0, low=94.0)
    assert exit_ == ("stop_loss", 95.0)  # la orden de mercado sale al gap


def test_take_profit_triggered_by_high():
    rm = _rm()
    exit_ = rm.check_candle_exit(_pos(rm), open_=100.0, high=104.5, low=99.0)
    assert exit_ == ("take_profit", 104.0)


def test_gap_up_fills_take_profit_at_open():
    rm = _rm()
    exit_ = rm.check_candle_exit(_pos(rm), open_=106.0, high=107.0, low=105.0)
    assert exit_ == ("take_profit", 106.0)  # la LIMIT se ejecuta mejor


def test_stop_wins_when_both_touched_same_candle():
    rm = _rm()
    exit_ = rm.check_candle_exit(_pos(rm), open_=100.0, high=105.0, low=97.0)
    assert exit_ == ("stop_loss", 98.0)  # convención conservadora


def test_no_exit_inside_range():
    rm = _rm()
    assert rm.check_candle_exit(_pos(rm), open_=100.0, high=103.0, low=99.0) is None


# ------------------------- simulate() de backtest -------------------------
class BuyOnce:
    """Estrategia estática: compra cuando la ventana alcanza `buy_at` velas."""
    name = "buy_once"

    def __init__(self, buy_at: int):
        self.buy_at = buy_at

    def generate_signal(self, candles):
        return Signal.BUY if len(candles) - 1 == self.buy_at else Signal.HOLD


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_simulate_closes_stop_touched_intracandle():
    # Compra al cierre de la vela 2 (100). La vela 3 toca 97 (SL=98) pero
    # CIERRA en 100: con la evaluación por cierre no habría salida.
    df = _df([
        [100, 100, 100, 100],
        [100, 100, 100, 100],
        [100, 101, 99, 100],
        [100, 101, 97, 100],
    ])
    res = simulate(df, "BTCUSDT", BuyOnce(buy_at=2), fee_pct=0.0)
    assert res["trades"] == 1 and res["wins"] == 0
    assert res["pnl"] == pytest.approx((98.0 - 100.0) * (20.0 / 100.0))  # qty 0.2


def test_simulate_closes_take_profit_touched_intracandle():
    df = _df([
        [100, 100, 100, 100],
        [100, 100, 100, 100],
        [100, 101, 99, 100],
        [100, 105, 99, 101],  # toca 104 (TP) aunque cierra en 101
    ])
    res = simulate(df, "BTCUSDT", BuyOnce(buy_at=2), fee_pct=0.0)
    assert res["trades"] == 1 and res["wins"] == 1
    assert res["pnl"] == pytest.approx((104.0 - 100.0) * (20.0 / 100.0))
