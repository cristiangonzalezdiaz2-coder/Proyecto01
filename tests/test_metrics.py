"""Pruebas de las métricas de rendimiento (sin red)."""
from src.analytics import compute_metrics, equity_curve


def _trades(pnls):
    return [{"pnl": p, "closed_at": f"2026-07-0{i+1}"} for i, p in enumerate(pnls)]


def test_empty():
    m = compute_metrics([])
    assert m["total_trades"] == 0
    assert m["profit_factor"] is None or m["profit_factor"] == 0.0
    assert m["max_drawdown"] == 0.0


def test_basic_counts_and_pnl():
    m = compute_metrics(_trades([10, -5, 8, -2]))
    assert m["total_trades"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 2
    assert m["win_rate"] == 50.0
    assert round(m["total_pnl"], 4) == 11.0
    assert round(m["expectancy"], 4) == 2.75


def test_profit_factor():
    # Ganancias 18, pérdidas 7 -> PF = 18/7
    m = compute_metrics(_trades([10, -5, 8, -2]))
    assert round(m["profit_factor"], 4) == round(18 / 7, 4)


def test_profit_factor_infinite_when_no_losses():
    m = compute_metrics(_trades([3, 4, 5]))
    assert m["profit_factor"] is None  # se representa como ∞ en la UI


def test_avg_win_loss_and_extremes():
    m = compute_metrics(_trades([10, -5, 8, -2]))
    assert round(m["avg_win"], 4) == 9.0     # (10+8)/2
    assert round(m["avg_loss"], 4) == -3.5   # (-5-2)/2
    assert m["best_trade"] == 10
    assert m["worst_trade"] == -5


def test_max_drawdown():
    # Equity: 0->10->4->12->2. Picos: 10 y 12. Mayor caída = 12-2 = 10.
    m = compute_metrics(_trades([10, -6, 8, -10]))
    assert round(m["max_drawdown"], 4) == 10.0
    # % respecto al pico (12): 10/12*100
    assert round(m["max_drawdown_pct"], 2) == round(10 / 12 * 100, 2)


def test_current_streak_positive_and_negative():
    assert compute_metrics(_trades([-1, 2, 3, 4]))["current_streak"] == 3
    assert compute_metrics(_trades([5, -1, -2]))["current_streak"] == -2


def test_equity_curve_shape():
    curve = equity_curve(_trades([10, -4, 6]))
    # Punto base + un punto por operación.
    assert len(curve) == 4
    assert curve[0]["equity"] == 0.0
    assert curve[1]["equity"] == 10.0
    assert curve[2]["equity"] == 6.0
    assert curve[3]["equity"] == 12.0
