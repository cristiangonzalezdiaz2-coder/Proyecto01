"""Pruebas de las estrategias RSI, MACD y Bollinger (sin red)."""
import numpy as np
import pandas as pd

from src.strategies import STRATEGIES, Signal, load_strategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.rsi import RSIStrategy, compute_rsi


def _df(prices):
    return pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": [1] * len(prices),
    })


# --------------------------- Registro ---------------------------
def test_all_strategies_registered_and_loadable():
    for name in ["ma_crossover", "rsi", "macd", "bollinger"]:
        assert name in STRATEGIES
        assert load_strategy(name, {}) is not None


# ----------------------------- RSI -----------------------------
def test_rsi_bounds():
    # Precios siempre subiendo -> RSI cercano a 100.
    rsi = compute_rsi(pd.Series(np.arange(1, 60, dtype=float)), 14)
    assert rsi.iloc[-1] > 90


def test_rsi_hold_insufficient_data():
    strat = RSIStrategy(period=14)
    assert strat.generate_signal(_df([1, 2, 3])) == Signal.HOLD


def test_rsi_buy_on_exit_from_oversold():
    # Caída fuerte (RSI baja a zona de sobreventa) y luego un rebote
    # que hace al RSI cruzar de nuevo por encima de 30.
    prices = list(np.linspace(100, 60, 30)) + [62, 66, 72]
    strat = RSIStrategy(period=14, oversold=30, overbought=70)
    # En algún punto del rebote debe generarse una señal BUY.
    signals = [strat.generate_signal(_df(prices[: i + 1])) for i in range(len(prices))]
    assert Signal.BUY in signals


def test_rsi_invalid_thresholds():
    try:
        RSIStrategy(oversold=70, overbought=30)
        assert False
    except ValueError:
        pass


# ----------------------------- MACD -----------------------------
def test_macd_buy_on_upcross():
    # Bajada prolongada seguida de subida fuerte -> MACD cruza al alza.
    prices = list(np.linspace(100, 70, 40)) + list(np.linspace(70, 110, 20))
    strat = MACDStrategy(fast=12, slow=26, signal=9)
    signals = [strat.generate_signal(_df(prices[: i + 1])) for i in range(len(prices))]
    assert Signal.BUY in signals


def test_macd_fast_must_be_less_than_slow():
    try:
        MACDStrategy(fast=26, slow=12)
        assert False
    except ValueError:
        pass


# --------------------------- Bollinger ---------------------------
def test_bollinger_sell_on_upper_breakout():
    # Precio estable y luego un pico que rompe la banda superior -> SELL.
    prices = [100.0] * 25 + [130.0]
    strat = BollingerStrategy(period=20, num_std=2.0)
    assert strat.generate_signal(_df(prices)) == Signal.SELL


def test_bollinger_buy_on_lower_breakout():
    prices = [100.0] * 25 + [70.0]
    strat = BollingerStrategy(period=20, num_std=2.0)
    assert strat.generate_signal(_df(prices)) == Signal.BUY


def test_bollinger_invalid_params():
    try:
        BollingerStrategy(period=1)
        assert False
    except ValueError:
        pass
