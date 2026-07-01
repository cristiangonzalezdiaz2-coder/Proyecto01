"""Estrategia RSI (Relative Strength Index) — reversión a la media.

El RSI mide la fuerza del movimiento reciente en una escala de 0 a 100.
- Por debajo de `oversold` (ej. 30): activo "sobrevendido" -> posible rebote.
- Por encima de `overbought` (ej. 70): activo "sobrecomprado" -> posible caída.

Para evitar señales repetidas, operamos en el CRUCE de salida de esas zonas:
- BUY cuando el RSI sale de la zona de sobreventa (cruza `oversold` hacia arriba).
- SELL cuando el RSI sale de la zona de sobrecompra (cruza `overbought` hacia abajo).
"""
import pandas as pd

from .base import Signal, Strategy, crossings_to_signals


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI con suavizado de Wilder (EMA con alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    # Si no hubo pérdidas, el RSI es 100 (evita división por cero -> NaN).
    rsi[avg_loss == 0] = 100.0
    return rsi


class RSIStrategy(Strategy):
    name = "rsi"

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        if not 0 < oversold < overbought < 100:
            raise ValueError("Se requiere 0 < oversold < overbought < 100.")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.period + 2:
            return Signal.HOLD

        rsi = compute_rsi(candles["close"], self.period)
        prev, now = rsi.iloc[-2], rsi.iloc[-1]
        if pd.isna(prev) or pd.isna(now):
            return Signal.HOLD

        # Cruce alcista saliendo de sobreventa.
        if prev <= self.oversold and now > self.oversold:
            return Signal.BUY
        # Cruce bajista saliendo de sobrecompra.
        if prev >= self.overbought and now < self.overbought:
            return Signal.SELL
        return Signal.HOLD

    def generate_signals(self, candles: pd.DataFrame) -> list[Signal]:
        """Versión vectorizada (O(n)): mismas señales que generate_signal."""
        rsi = compute_rsi(candles["close"], self.period)
        prev = rsi.shift(1)
        buy = (prev <= self.oversold) & (rsi > self.oversold)
        sell = (prev >= self.overbought) & (rsi < self.overbought)
        # Guarda de historia mínima: len < period + 2 -> HOLD.
        return crossings_to_signals(buy, sell, min_index=self.period + 1)
