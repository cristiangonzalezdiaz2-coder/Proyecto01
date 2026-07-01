"""Backtest de estrategias sobre datos históricos de MEXC.

Descarga velas reales (endpoint público, no requiere API key) y simula
la estrategia con las mismas reglas de riesgo que el bot en vivo.

Ejemplos:
    # Una estrategia concreta con sus parámetros:
    python backtest.py --strategy rsi --params '{"period": 14}'
    python backtest.py --strategy ma_crossover --params '{"fast_period": 9, "slow_period": 21}'

    # Comparar TODAS las estrategias (parámetros por defecto) sobre los mismos datos:
    python backtest.py --compare --symbol BTCUSDT --interval 1h --limit 1000
"""
import argparse
import json

from src.config import RiskConfig
from src.logger import get_logger
from src.mexc import MexcSpotClient
from src.risk import RiskManager
from src.strategies import STRATEGIES, Signal, load_strategy
from src.trading.engine import klines_to_df

log = get_logger("backtest")


def simulate(df, symbol: str, strategy) -> dict:
    """Corre la estrategia vela a vela y devuelve métricas del resultado."""
    risk = RiskManager(RiskConfig())
    trades = wins = 0

    for i in range(1, len(df)):
        window = df.iloc[: i + 1]
        price = float(window["close"].iloc[-1])

        # Salidas por stop-loss / take-profit.
        for pos in list(risk.open_positions):
            if risk.should_close(pos, price):
                pnl = risk.register_close(pos, price)
                trades += 1
                wins += 1 if pnl > 0 else 0

        signal = strategy.generate_signal(window)
        if signal == Signal.BUY and risk.can_open():
            risk.register_open(risk.build_position(symbol, price))
        elif signal == Signal.SELL:
            for pos in list(risk.open_positions):
                pnl = risk.register_close(pos, price)
                trades += 1
                wins += 1 if pnl > 0 else 0

    win_rate = (wins / trades * 100) if trades else 0.0
    return {"trades": trades, "wins": wins, "win_rate": win_rate, "pnl": risk.daily_pnl}


def fetch(symbol: str, interval: str, limit: int):
    client = MexcSpotClient()  # solo datos públicos
    return klines_to_df(client.get_klines(symbol, interval, limit=limit))


def run_single(symbol, interval, limit, name, params):
    df = fetch(symbol, interval, limit)
    strat = load_strategy(name, params)
    res = simulate(df, symbol, strat)
    log.info("=== Backtest %s | %s %s (%d velas) ===", name, symbol, interval, len(df))
    log.info("Operaciones: %d | Ganadoras: %d (%.1f%%)", res["trades"], res["wins"], res["win_rate"])
    log.info("PnL total (aprox, USDT): %.4f", res["pnl"])
    log.info("NOTA: sin comisiones ni slippage. Solo orientativo.")


def run_compare(symbol, interval, limit):
    df = fetch(symbol, interval, limit)
    log.info("=== Comparativa de estrategias | %s %s (%d velas) ===", symbol, interval, len(df))
    log.info("%-14s %8s %8s %10s %12s", "estrategia", "ops", "aciertos", "% acierto", "PnL(USDT)")
    log.info("-" * 56)
    rows = []
    for name in STRATEGIES:
        strat = load_strategy(name, {})  # parámetros por defecto de cada una
        res = simulate(df, symbol, strat)
        rows.append((name, res))
    # Ordenar por PnL descendente.
    rows.sort(key=lambda r: r[1]["pnl"], reverse=True)
    for name, res in rows:
        log.info("%-14s %8d %8d %9.1f%% %12.4f",
                 name, res["trades"], res["wins"], res["win_rate"], res["pnl"])
    log.info("-" * 56)
    log.info("NOTA: sin comisiones ni slippage. Solo orientativo.")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest de estrategias MEXC")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--strategy", default="ma_crossover", choices=list(STRATEGIES),
                   help="Estrategia a probar (ignorado con --compare)")
    p.add_argument("--params", default="{}",
                   help='Parámetros JSON, ej: \'{"period": 14}\'')
    p.add_argument("--compare", action="store_true",
                   help="Comparar todas las estrategias sobre los mismos datos")
    args = p.parse_args()

    if args.compare:
        run_compare(args.symbol, args.interval, args.limit)
    else:
        params = json.loads(args.params)
        run_single(args.symbol, args.interval, args.limit, args.strategy, params)


if __name__ == "__main__":
    main()
