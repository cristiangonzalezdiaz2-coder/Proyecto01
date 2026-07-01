"""Backtest sencillo de la estrategia sobre datos históricos de MEXC.

Descarga velas reales (endpoint público, no requiere API key) y simula
la estrategia con las mismas reglas de riesgo que el bot en vivo.

Uso:
    python backtest.py --symbol BTCUSDT --interval 1h --limit 500
"""
import argparse

from src.config import RiskConfig
from src.logger import get_logger
from src.mexc import MexcSpotClient
from src.risk import RiskManager
from src.strategies import Signal, load_strategy
from src.trading.engine import klines_to_df

log = get_logger("backtest")


def run_backtest(symbol: str, interval: str, limit: int,
                 fast: int, slow: int) -> None:
    client = MexcSpotClient()  # solo datos públicos
    raw = client.get_klines(symbol, interval, limit=limit)
    df = klines_to_df(raw)

    strat = load_strategy("ma_crossover", {"fast_period": fast, "slow_period": slow})
    risk = RiskManager(RiskConfig())

    trades = 0
    wins = 0
    # Recorremos vela a vela simulando el paso del tiempo.
    for i in range(slow + 1, len(df)):
        window = df.iloc[: i + 1]
        price = float(window["close"].iloc[-1])

        for pos in list(risk.open_positions):
            reason = risk.should_close(pos, price)
            if reason:
                pnl = risk.register_close(pos, price)
                trades += 1
                wins += 1 if pnl > 0 else 0

        signal = strat.generate_signal(window)
        if signal == Signal.BUY and risk.can_open():
            risk.register_open(risk.build_position(symbol, price))
        elif signal == Signal.SELL:
            for pos in list(risk.open_positions):
                pnl = risk.register_close(pos, price)
                trades += 1
                wins += 1 if pnl > 0 else 0

    win_rate = (wins / trades * 100) if trades else 0.0
    log.info("=== Resultado backtest %s %s ===", symbol, interval)
    log.info("Operaciones cerradas: %d | Ganadoras: %d (%.1f%%)", trades, wins, win_rate)
    log.info("PnL total (aprox, en USDT): %.4f", risk.daily_pnl)
    log.info("NOTA: backtest simplificado, sin comisiones ni slippage. Solo orientativo.")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest MA crossover")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--fast", type=int, default=9)
    p.add_argument("--slow", type=int, default=21)
    args = p.parse_args()
    run_backtest(args.symbol, args.interval, args.limit, args.fast, args.slow)


if __name__ == "__main__":
    main()
