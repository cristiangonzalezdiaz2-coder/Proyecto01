"""Pruebas de la optimización walk-forward (datos sintéticos, sin red)."""
import numpy as np
import pandas as pd
import pytest

from src.optimize import default_grid, walk_forward
from src.optimize.walkforward import _param_combos, _valid_combos, run_segment
from src.strategies import load_strategy


def _synthetic_df(n=240, seed=1):
    rng = np.random.default_rng(seed)
    # Tendencia suave + ruido para que haya cruces y operaciones.
    prices = 100 + np.cumsum(rng.normal(0, 1, n)) + np.linspace(0, 15, n)
    prices = np.abs(prices) + 1
    return pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "volume": np.ones(n),
    })


def test_param_combos_count():
    grid = {"a": [1, 2], "b": [3, 4, 5]}
    combos = _param_combos(grid)
    assert len(combos) == 6
    assert {"a": 1, "b": 3} in combos


def test_valid_combos_discards_invalid():
    # fast_period >= slow_period no es válido y debe descartarse.
    grid = {"fast_period": [5, 30], "slow_period": [20]}
    valid = _valid_combos("ma_crossover", grid)
    assert {"fast_period": 5, "slow_period": 20} in valid
    assert {"fast_period": 30, "slow_period": 20} not in valid


def test_run_segment_only_opens_in_window():
    df = _synthetic_df()
    strat = load_strategy("ma_crossover", {"fast_period": 5, "slow_period": 20})
    # Ventana vacía (start==end) => no debería abrir ninguna operación.
    pnls = run_segment(df, "BTCUSDT", strat, 50, 50)
    assert pnls == []


def test_walk_forward_structure():
    df = _synthetic_df(n=240)
    res = walk_forward(df, "BTCUSDT", "ma_crossover", folds=4, objective="pnl", min_trades=1)
    assert len(res["folds"]) == 4
    for fold in res["folds"]:
        # El test empieza donde acaba el entrenamiento (walk-forward correcto).
        assert fold["test_range"][0] == fold["train_range"][1]
        assert "best_params" in fold and "out_of_sample" in fold
    assert "combined_oos" in res
    assert "overfit_baseline" in res


def test_walk_forward_no_lookahead_ranges():
    df = _synthetic_df(n=240)
    res = walk_forward(df, "BTCUSDT", "rsi", folds=3, objective="sharpe", min_trades=1)
    # Cada tramo de test es posterior a su entrenamiento y no se solapan entre folds.
    prev_end = 0
    for fold in res["folds"]:
        ts, te = fold["test_range"]
        assert ts >= prev_end
        assert te > ts
        prev_end = te


def test_insufficient_data_raises():
    df = _synthetic_df(n=50)
    with pytest.raises(ValueError):
        walk_forward(df, "BTCUSDT", "ma_crossover", folds=10)


def test_default_grid_known_strategies():
    for name in ["ma_crossover", "rsi", "macd", "bollinger"]:
        assert isinstance(default_grid(name), dict)
    with pytest.raises(ValueError):
        default_grid("desconocida")
