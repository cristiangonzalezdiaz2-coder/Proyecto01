"""Pruebas básicas de la estrategia y la gestión de riesgo (sin red)."""
import pandas as pd

from src.config import RiskConfig
from src.risk import RiskManager
from src.strategies import Signal
from src.strategies.ma_crossover import MACrossoverStrategy


def _df(prices):
    return pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": [1] * len(prices),
    })


def test_ma_crossover_buy():
    # Precios planos y un salto en la ÚLTIMA vela -> el cruce alcista
    # ocurre justo en esa vela.
    prices = [10] * 24 + [11]
    strat = MACrossoverStrategy(fast_period=3, slow_period=5)
    assert strat.generate_signal(_df(prices)) == Signal.BUY


def test_ma_crossover_hold_when_insufficient_data():
    strat = MACrossoverStrategy(fast_period=9, slow_period=21)
    assert strat.generate_signal(_df([10, 11, 12])) == Signal.HOLD


def test_fast_must_be_less_than_slow():
    try:
        MACrossoverStrategy(fast_period=21, slow_period=9)
        assert False, "debería lanzar ValueError"
    except ValueError:
        pass


def test_risk_stop_loss_and_daily_halt():
    cfg = RiskConfig(quote_per_trade=100, stop_loss_pct=0.1,
                     take_profit_pct=0.2, max_daily_loss=5, max_open_positions=1)
    rm = RiskManager(cfg)
    assert rm.can_open()

    pos = rm.build_position("BTCUSDT", 100.0)
    rm.register_open(pos)
    assert not rm.can_open()  # ya hay 1 abierta (máximo 1)

    # Precio cae al stop-loss (90).
    assert rm.should_close(pos, 90.0) == "stop_loss"
    pnl = rm.register_close(pos, 90.0)
    assert pnl < 0
    assert rm.halted  # pérdida (10) supera max_daily_loss (5)
    assert not rm.can_open()
