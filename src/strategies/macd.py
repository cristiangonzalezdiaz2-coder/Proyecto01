"""Estrategia MACD (Moving Average Convergence Divergence) — seguimiento de momentum.

MACD = EMA(rápida) - EMA(lenta).  Señal = EMA(MACD, signal_period).
- BUY cuando la línea MACD cruza por ENCIMA de la línea de señal (momentum alcista).
- SELL cuando la línea MACD cruza por DEBAJO de la línea de señal (momentum bajista).
"""
import pandas as pd

from .base import Signal, Strategy


def compute_macd(close: pd.Series, fast: int, slow: int, signal: int):
    """Devuelve (macd_line, signal_line)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


class MACDStrategy(Strategy):
    name = "macd"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if fast >= slow:
            raise ValueError("fast debe ser menor que slow.")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        # Necesitamos historia suficiente para que las EMAs se estabilicen.
        if len(candles) < self.slow + self.signal:
            return Signal.HOLD

        macd_line, signal_line = compute_macd(
            candles["close"], self.fast, self.slow, self.signal
        )
        macd_prev, macd_now = macd_line.iloc[-2], macd_line.iloc[-1]
        sig_prev, sig_now = signal_line.iloc[-2], signal_line.iloc[-1]

        crossed_up = macd_prev <= sig_prev and macd_now > sig_now
        crossed_down = macd_prev >= sig_prev and macd_now < sig_now

        if crossed_up:
            return Signal.BUY
        if crossed_down:
            return Signal.SELL
        return Signal.HOLD
