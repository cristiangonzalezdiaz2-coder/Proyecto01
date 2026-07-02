"""Estrategia de Bandas de Bollinger — reversión por volatilidad.

Banda media = SMA(period).  Bandas = media ± num_std * desviación estándar.
Cuando el precio se aleja mucho de la media tiende a volver ("reversión").
- BUY cuando el precio cruza por DEBAJO de la banda inferior (sobreventa).
- SELL cuando el precio cruza por ENCIMA de la banda superior (sobrecompra).
"""
import pandas as pd

from .base import Signal, Strategy, crossings_to_signals


def compute_bands(close: pd.Series, period: int, num_std: float):
    """Devuelve (media, banda_superior, banda_inferior)."""
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


class BollingerStrategy(Strategy):
    name = "bollinger"

    def __init__(self, period: int = 20, num_std: float = 2.0):
        if period < 2:
            raise ValueError("period debe ser >= 2.")
        if num_std <= 0:
            raise ValueError("num_std debe ser positivo.")
        self.period = period
        self.num_std = num_std

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.period + 1:
            return Signal.HOLD

        close = candles["close"]
        _, upper, lower = compute_bands(close, self.period, self.num_std)

        price_prev, price_now = close.iloc[-2], close.iloc[-1]
        low_prev, low_now = lower.iloc[-2], lower.iloc[-1]
        up_prev, up_now = upper.iloc[-2], upper.iloc[-1]

        if pd.isna(low_prev) or pd.isna(up_prev):
            return Signal.HOLD

        # Cruce por debajo de la banda inferior -> sobreventa -> compra.
        if price_prev >= low_prev and price_now < low_now:
            return Signal.BUY
        # Cruce por encima de la banda superior -> sobrecompra -> venta.
        if price_prev <= up_prev and price_now > up_now:
            return Signal.SELL
        return Signal.HOLD

    def generate_signals(self, candles: pd.DataFrame) -> list[Signal]:
        """Versión vectorizada (O(n)): mismas señales que generate_signal."""
        close = candles["close"]
        _, upper, lower = compute_bands(close, self.period, self.num_std)
        close_prev = close.shift(1)
        buy = (close_prev >= lower.shift(1)) & (close < lower)
        sell = (close_prev <= upper.shift(1)) & (close > upper)
        # Guarda de historia mínima: len < period + 1 -> HOLD.
        return crossings_to_signals(buy, sell, min_index=self.period)
