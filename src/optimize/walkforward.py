"""Optimización walk-forward de los parámetros de una estrategia.

Idea (validación honesta, sin autoengaño):
  1. Se divide el histórico en tramos consecutivos.
  2. En cada "fold" se optimizan los parámetros SOLO con los datos de
     entrenamiento (in-sample) y se evalúan en el tramo SIGUIENTE, que el
     optimizador no vio (out-of-sample / OOS).
  3. Se agregan todos los resultados OOS: esa es la estimación realista de cómo
     generalizarían los parámetros a datos nuevos.

Se usa una ventana de entrenamiento expansiva (anchored): el entrenamiento
crece con cada fold y el test es siempre el tramo inmediatamente posterior.

Los parámetros "buenos" son los que rinden bien OOS de forma consistente, NO
los que dieron el mayor PnL sobre todo el histórico (eso es sobreajuste).
"""
import itertools

import pandas as pd

from ..analytics import compute_metrics
from ..config import RiskConfig
from ..risk import RiskManager
from ..strategies import Signal, compute_signals, load_strategy


# Rejillas de parámetros por defecto para cada estrategia.
DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "ma_crossover": {"fast_period": [5, 9, 12], "slow_period": [20, 26, 34]},
    "rsi": {"period": [7, 14, 21], "oversold": [25, 30], "overbought": [70, 75]},
    "macd": {"fast": [8, 12], "slow": [21, 26], "signal": [9]},
    "bollinger": {"period": [14, 20, 30], "num_std": [2.0, 2.5]},
}


def default_grid(strategy_name: str) -> dict[str, list]:
    if strategy_name not in DEFAULT_GRIDS:
        raise ValueError(f"No hay rejilla por defecto para '{strategy_name}'.")
    return DEFAULT_GRIDS[strategy_name]


def _param_combos(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def _valid_combos(strategy_name: str, grid: dict[str, list]) -> list[dict]:
    """Combinaciones que construyen una estrategia válida (descarta imposibles)."""
    valid = []
    for params in _param_combos(grid):
        try:
            load_strategy(strategy_name, params)
        except (ValueError, TypeError):
            continue  # p. ej. fast_period >= slow_period
        valid.append(params)
    return valid


def _metrics_from_pnls(pnls: list[float]) -> dict:
    return compute_metrics([{"pnl": p} for p in pnls])


def _objective_value(metrics: dict, objective: str, min_trades: int) -> float:
    """Puntúa un conjunto de métricas según el objetivo elegido.

    Exige un mínimo de operaciones para no premiar a quien operó una vez y ganó.
    """
    if metrics["total_trades"] < min_trades:
        return float("-inf")
    if objective == "pnl":
        return metrics["total_pnl"]
    if objective == "expectancy":
        return metrics["expectancy"]
    if objective == "sharpe":
        return metrics["sharpe"]
    if objective == "win_rate":
        return metrics["win_rate"]
    if objective == "profit_factor":
        pf = metrics["profit_factor"]
        return 1e9 if pf is None else pf  # None = sin pérdidas (ideal)
    return metrics["total_pnl"]


def run_segment(df_full: pd.DataFrame, symbol: str, strategy,
                open_start: int, open_end: int,
                signals: list | None = None) -> list[float]:
    """Simula la estrategia permitiendo ABRIR solo en [open_start, open_end).

    Los indicadores usan todo el historial hasta cada vela (nada de futuro),
    pero las entradas se restringen al segmento evaluado, de modo que el
    resultado se atribuye limpiamente a ese tramo. Las posiciones que queden
    abiertas al final del segmento se cierran al último precio.

    `signals` permite pasar las señales por vela ya calculadas (una por
    índice de df_full); si no se pasan, se calculan aquí en una sola pasada
    vectorizada (O(n) en vez del O(n²) de las ventanas crecientes).
    """
    risk = RiskManager(RiskConfig())
    pnls: list[float] = []
    if signals is None:
        signals = compute_signals(strategy, df_full)
    opens = df_full["open"].to_numpy(dtype=float)
    highs = df_full["high"].to_numpy(dtype=float)
    lows = df_full["low"].to_numpy(dtype=float)
    closes = df_full["close"].to_numpy(dtype=float)

    last_price = None
    for i in range(1, open_end):
        price = float(closes[i])
        last_price = price

        # Salidas por SL/TP contra el RANGO de la vela (permitidas siempre).
        for pos in list(risk.open_positions):
            exit_ = risk.check_candle_exit(pos, float(opens[i]),
                                           float(highs[i]), float(lows[i]))
            if exit_:
                _reason, exit_price = exit_
                pnls.append(risk.register_close(pos, exit_price))

        # Trailing stop (si está activo): efectivo desde la vela siguiente.
        for pos in risk.open_positions:
            risk.update_trailing(pos, float(highs[i]))

        # Entradas solo dentro del segmento evaluado.
        if open_start <= i < open_end:
            signal = signals[i]
            if signal == Signal.BUY and risk.can_open():
                risk.register_open(risk.build_position(symbol, price))
            elif signal == Signal.SELL:
                for pos in list(risk.open_positions):
                    pnls.append(risk.register_close(pos, price))

    # Cerrar lo que quede abierto al final del segmento.
    if last_price is not None:
        for pos in list(risk.open_positions):
            pnls.append(risk.register_close(pos, last_price))
    return pnls


def _best_params(df, symbol, combos, signals_by_combo, start, end, objective, min_trades):
    best = None
    for params, signals in zip(combos, signals_by_combo):
        pnls = run_segment(df, symbol, None, start, end, signals=signals)
        metrics = _metrics_from_pnls(pnls)
        score = _objective_value(metrics, objective, min_trades)
        if best is None or score > best["score"]:
            best = {"params": params, "score": score, "metrics": metrics}
    return best


def walk_forward(df: pd.DataFrame, symbol: str, strategy_name: str,
                 grid: dict[str, list] | None = None, folds: int = 5,
                 objective: str = "pnl", min_trades: int = 3) -> dict:
    """Ejecuta la optimización walk-forward y devuelve resultados por fold y OOS.

    Devuelve un dict con:
      - folds: lista con, por fold, los rangos, los mejores parámetros in-sample
        y sus métricas out-of-sample.
      - combined_oos: métricas agregadas de todos los tramos OOS (lo importante).
      - overfit_baseline: mejores parámetros y métricas optimizando sobre TODO el
        histórico (referencia de cuánto engaña el sobreajuste).
    """
    grid = grid or default_grid(strategy_name)
    combos = _valid_combos(strategy_name, grid)
    if not combos:
        raise ValueError("La rejilla no produce ninguna combinación válida.")

    # Precalcular las señales de cada combinación UNA sola vez (vectorizado):
    # todos los folds reutilizan la misma lista, en vez de recomputar los
    # indicadores con ventanas crecientes en cada tramo (O(n²) -> O(n)).
    signals_by_combo = [
        compute_signals(load_strategy(strategy_name, params), df) for params in combos
    ]

    n = len(df)
    seg = n // (folds + 1)
    if seg < 20:
        raise ValueError(
            f"Datos insuficientes ({n} velas) para {folds} folds. "
            "Usa más velas (--limit) o menos folds."
        )

    fold_results = []
    all_oos_pnls: list[float] = []
    for f in range(folds):
        train_start, train_end = 0, (f + 1) * seg          # ventana expansiva
        test_start = train_end
        test_end = (train_end + seg) if f < folds - 1 else n

        best = _best_params(df, symbol, combos, signals_by_combo,
                            train_start, train_end, objective, min_trades)
        # Evaluar los mejores parámetros en el tramo OOS.
        best_signals = signals_by_combo[combos.index(best["params"])]
        oos_pnls = run_segment(df, symbol, None, test_start, test_end,
                               signals=best_signals)
        oos_metrics = _metrics_from_pnls(oos_pnls)
        all_oos_pnls.extend(oos_pnls)

        fold_results.append({
            "fold": f + 1,
            "train_range": [train_start, train_end],
            "test_range": [test_start, test_end],
            "best_params": best["params"],
            "in_sample": best["metrics"],
            "out_of_sample": oos_metrics,
        })

    # Referencia de sobreajuste: optimizar sobre TODO el histórico.
    overfit = _best_params(df, symbol, combos, signals_by_combo, 0, n,
                           objective, min_trades)

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "objective": objective,
        "folds": fold_results,
        "combined_oos": _metrics_from_pnls(all_oos_pnls),
        "overfit_baseline": {"params": overfit["params"], "metrics": overfit["metrics"]},
    }
