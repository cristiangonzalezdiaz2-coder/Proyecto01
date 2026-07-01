"""Métricas de rendimiento a partir del historial de operaciones cerradas.

Todas las funciones reciben `trades`: una lista de dicts con al menos las
claves 'pnl' y 'closed_at', en orden cronológico ascendente (la más antigua
primero). No dependen de la base de datos, así que son fáciles de probar.
"""
from math import sqrt


def equity_curve(trades: list[dict], starting_equity: float = 0.0) -> list[dict]:
    """Curva de equity acumulada tras cada operación.

    Devuelve una lista de puntos {i, equity, closed_at}, empezando en el
    capital inicial (punto 0) para que el gráfico arranque en la línea base.
    """
    curve = [{"i": 0, "equity": starting_equity, "closed_at": None}]
    equity = starting_equity
    for idx, t in enumerate(trades, start=1):
        equity += float(t.get("pnl", 0.0))
        curve.append({"i": idx, "equity": equity, "closed_at": t.get("closed_at")})
    return curve


def _max_drawdown(pnls: list[float], starting_equity: float = 0.0):
    """Máxima caída desde un pico de equity (absoluta y en %)."""
    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0
    max_dd_pct = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100) if peak > 0 else 0.0
    return max_dd, max_dd_pct


def _current_streak(pnls: list[float]) -> int:
    """Racha actual: +n operaciones ganadoras seguidas o -n perdedoras."""
    if not pnls:
        return 0
    streak = 0
    last_sign = None
    for pnl in reversed(pnls):
        sign = 1 if pnl > 0 else (-1 if pnl < 0 else 0)
        if sign == 0:
            break
        if last_sign is None or sign == last_sign:
            streak += sign
            last_sign = sign
        else:
            break
    return streak


def compute_metrics(trades: list[dict], starting_equity: float = 0.0) -> dict:
    """Calcula el conjunto de métricas de rendimiento."""
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    total = len(pnls)

    if total == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "expectancy": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": None, "best_trade": 0.0, "worst_trade": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0, "current_streak": 0,
            "sharpe": 0.0,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    total_pnl = sum(pnls)

    # Profit factor: None (∞) si no hay pérdidas pero sí ganancias.
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = None if gross_profit > 0 else 0.0

    # Ratio de Sharpe simple (por operación, sin anualizar): media/desv. típica.
    mean = total_pnl / total
    if total > 1:
        variance = sum((p - mean) ** 2 for p in pnls) / (total - 1)
        std = sqrt(variance)
        sharpe = (mean / std) if std > 0 else 0.0
    else:
        sharpe = 0.0

    max_dd, max_dd_pct = _max_drawdown(pnls, starting_equity)

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total * 100,
        "total_pnl": total_pnl,
        "expectancy": mean,
        "avg_win": (gross_profit / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": profit_factor,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "current_streak": _current_streak(pnls),
        "sharpe": sharpe,
    }
