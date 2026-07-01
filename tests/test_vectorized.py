"""Pruebas de paridad de las señales vectorizadas.

Contrato: generate_signals(df)[i] debe coincidir EXACTAMENTE con
generate_signal(df.iloc[:i+1]) para todo i. Los indicadores son causales
(rolling/ewm solo miran hacia atrás), así que calcularlos una vez sobre todo
el histórico da los mismos valores que recalcularlos por ventana; estas
pruebas verifican esa equivalencia (incluidas las guardas de historia mínima)
sobre datos pseudoaleatorios con cruces reales.
"""
import numpy as np
import pandas as pd
import pytest

from src.strategies import STRATEGIES, compute_signals, load_strategy
from src.strategies.base import Signal


def _random_walk_df(n=150, seed=7):
    rng = np.random.default_rng(seed)
    # Paseo aleatorio con volatilidad suficiente para provocar cruces.
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": 1.0})


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_vectorized_matches_per_window(name):
    df = _random_walk_df()
    strat = load_strategy(name, {})
    vectorized = strat.generate_signals(df)
    assert len(vectorized) == len(df)
    for i in range(len(df)):
        expected = strat.generate_signal(df.iloc[: i + 1])
        assert vectorized[i] == expected, (
            f"{name}: divergencia en i={i}: vectorizado={vectorized[i]}, "
            f"por-ventana={expected}"
        )


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_vectorized_produces_some_signals(name):
    # Sanidad: con 150 velas de paseo aleatorio debe haber alguna señal
    # (si todo fuera HOLD, la paridad se cumpliría trivialmente).
    df = _random_walk_df(seed=11)
    strat = load_strategy(name, {})
    signals = strat.generate_signals(df)
    assert any(s is not Signal.HOLD for s in signals)


def test_compute_signals_falls_back_for_plain_strategies():
    class OddBuyer:  # estrategia externa sin generate_signals
        def generate_signal(self, candles):
            return Signal.BUY if len(candles) % 2 == 1 else Signal.HOLD

    df = _random_walk_df(n=6)
    signals = compute_signals(OddBuyer(), df)
    assert signals == [Signal.BUY, Signal.HOLD] * 3
