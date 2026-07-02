"""Estrategia de cruce de medias móviles (moving average crossover).

- Señal BUY cuando la media rápida cruza por ENCIMA de la lenta.
- Señal SELL cuando la media rápida cruza por DEBAJO de la lenta.
- HOLD en cualquier otro caso.
"""
import pandas as pd

from .base import Signal, Strategy, crossings_to_signals


class MACrossoverStrategy(Strategy):
    name = "ma_crossover"

    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        if fast_period >= slow_period:
            raise ValueError("fast_period debe ser menor que slow_period.")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.slow_period + 1:
            return Signal.HOLD

        close = candles["close"]
        fast = close.rolling(self.fast_period).mean()
        slow = close.rolling(self.slow_period).mean()

        # Comparamos la vela actual con la anterior para detectar el cruce.
        fast_prev, fast_now = fast.iloc[-2], fast.iloc[-1]
        slow_prev, slow_now = slow.iloc[-2], slow.iloc[-1]

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        if crossed_up:
            return Signal.BUY
        if crossed_down:
            return Signal.SELL
        return Signal.HOLD

    def generate_signals(self, candles: pd.DataFrame) -> list[Signal]:
        """Versión vectorizada (O(n)): mismas señales que generate_signal."""
        close = candles["close"]
        fast = close.rolling(self.fast_period).mean()
        slow = close.rolling(self.slow_period).mean()
        fast_prev, slow_prev = fast.shift(1), slow.shift(1)
        buy = (fast_prev <= slow_prev) & (fast > slow)
        sell = (fast_prev >= slow_prev) & (fast < slow)
        # Guarda de historia mínima: len < slow_period + 1 -> HOLD.
        return crossings_to_signals(buy, sell, min_index=self.slow_period)
