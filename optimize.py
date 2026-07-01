"""Optimización walk-forward de parámetros sobre datos históricos de MEXC.

Optimiza los parámetros de una estrategia validando SIEMPRE en datos que el
optimizador no vio (out-of-sample), para evitar el sobreajuste.

Ejemplos:
    python optimize.py --strategy ma_crossover --symbol BTCUSDT --interval 1h --limit 1000
    python optimize.py --strategy rsi --objective sharpe --folds 6
    python optimize.py --strategy macd --objective profit_factor

Objetivos disponibles: pnl, expectancy, sharpe, profit_factor, win_rate
"""
import argparse

from src.logger import get_logger
from src.mexc import MexcSpotClient
from src.optimize import default_grid, walk_forward
from src.strategies import STRATEGIES
from src.trading.engine import klines_to_df

log = get_logger("optimize")


def _fmt_pf(pf) -> str:
    return "∞" if pf is None else f"{pf:.2f}"


def _print_metrics(prefix: str, m: dict) -> None:
    log.info("%s ops=%d | acierto=%.1f%% | PnL=%.4f | expectancy=%.4f | "
             "PF=%s | Sharpe=%.2f | DD=%.4f",
             prefix, m["total_trades"], m["win_rate"], m["total_pnl"],
             m["expectancy"], _fmt_pf(m["profit_factor"]), m["sharpe"], m["max_drawdown"])


def main() -> None:
    p = argparse.ArgumentParser(description="Optimización walk-forward MEXC")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--limit", type=int, default=1000, help="Nº de velas históricas")
    p.add_argument("--strategy", default="ma_crossover", choices=list(STRATEGIES))
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--objective", default="pnl",
                   choices=["pnl", "expectancy", "sharpe", "profit_factor", "win_rate"])
    p.add_argument("--min-trades", type=int, default=3,
                   help="Mínimo de operaciones para considerar válidos unos parámetros")
    args = p.parse_args()

    log.info("Descargando %d velas de %s %s...", args.limit, args.symbol, args.interval)
    client = MexcSpotClient()  # datos públicos, sin API key
    df = klines_to_df(client.get_klines(args.symbol, args.interval, limit=args.limit))

    log.info("Optimizando '%s' | objetivo=%s | folds=%d | rejilla=%s",
             args.strategy, args.objective, args.folds, default_grid(args.strategy))

    res = walk_forward(df, args.symbol, args.strategy, folds=args.folds,
                       objective=args.objective, min_trades=args.min_trades)

    log.info("=" * 70)
    for fold in res["folds"]:
        tr, te = fold["train_range"], fold["test_range"]
        log.info("Fold %d | entrena velas [%d:%d] -> test [%d:%d] | mejores params: %s",
                 fold["fold"], tr[0], tr[1], te[0], te[1], fold["best_params"])
        _print_metrics("   in-sample :", fold["in_sample"])
        _print_metrics("   OUT-SAMPLE:", fold["out_of_sample"])
    log.info("=" * 70)

    _print_metrics("RESULTADO OOS combinado (lo que importa):", res["combined_oos"])
    ob = res["overfit_baseline"]
    log.info("--- Referencia de sobreajuste (optimizar sobre TODO el histórico) ---")
    log.info("   params: %s", ob["params"])
    _print_metrics("   in-sample total :", ob["metrics"])
    log.info("Si el PnL OOS combinado es MUCHO menor que el in-sample total, "
             "esos parámetros están sobreajustados.")
    log.info("NOTA: sin comisiones ni slippage. Orientativo, no es garantía de resultados.")


if __name__ == "__main__":
    main()
