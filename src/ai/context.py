"""Construcción del contexto de mercado que se envía al agente de IA.

Resume las velas en un JSON compacto: precio, retornos por ventana,
indicadores técnicos (reutilizando los cálculos de las estrategias) y las
últimas velas OHLCV. Mandar un resumen en vez del histórico completo
mantiene el prompt pequeño (menos coste y latencia) sin perder la
información que el modelo necesita para decidir.
"""
import pandas as pd

from ..strategies.bollinger import compute_bands
from ..strategies.macd import compute_macd
from ..strategies.rsi import compute_rsi


def _pct(series: pd.Series, periods: int) -> float | None:
    """Retorno porcentual sobre las últimas `periods` velas (None si no hay historia)."""
    if len(series) <= periods:
        return None
    prev = series.iloc[-periods - 1]
    if prev == 0:
        return None
    return round(float((series.iloc[-1] / prev - 1) * 100), 3)


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def build_market_context(symbol: str, interval: str, candles: pd.DataFrame,
                         recent_candles: int = 20) -> dict:
    """Devuelve un dict serializable con el estado actual del mercado."""
    close = candles["close"]
    price = float(close.iloc[-1])

    rsi = compute_rsi(close, period=14)
    macd_line, signal_line = compute_macd(close, fast=12, slow=26, signal=9)
    histogram = macd_line - signal_line
    middle, upper, lower = compute_bands(close, period=20, num_std=2.0)

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    returns = close.pct_change()
    # Volatilidad de las últimas 20 velas, en % por vela.
    volatility = returns.iloc[-20:].std() * 100 if len(returns) >= 21 else None

    volume = candles["volume"]
    avg_volume = volume.iloc[-20:].mean() if len(volume) >= 20 else None
    volume_ratio = (
        float(volume.iloc[-1] / avg_volume) if avg_volume not in (None, 0) else None
    )

    band_width = upper.iloc[-1] - lower.iloc[-1]
    band_position = (
        float((price - lower.iloc[-1]) / band_width) if band_width and not pd.isna(band_width) else None
    )

    tail = candles.iloc[-recent_candles:]
    recent = [
        {
            "t": str(ts),
            "o": _round(row["open"]),
            "h": _round(row["high"]),
            "l": _round(row["low"]),
            "c": _round(row["close"]),
            "v": _round(row["volume"], 2),
        }
        for ts, row in tail.iterrows()
    ]

    return {
        "symbol": symbol,
        "interval": interval,
        "last_close_time": str(candles.index[-1]),
        "price": _round(price),
        "returns_pct": {
            "1_vela": _pct(close, 1),
            "5_velas": _pct(close, 5),
            "20_velas": _pct(close, 20),
            "50_velas": _pct(close, 50),
        },
        "indicators": {
            "rsi_14": _round(rsi.iloc[-1], 2),
            "macd": _round(macd_line.iloc[-1]),
            "macd_signal": _round(signal_line.iloc[-1]),
            "macd_histogram": _round(histogram.iloc[-1]),
            "sma_20": _round(sma20.iloc[-1]),
            "sma_50": _round(sma50.iloc[-1]),
            "bollinger_upper": _round(upper.iloc[-1]),
            "bollinger_lower": _round(lower.iloc[-1]),
            # 0 = pegado a la banda inferior, 1 = a la superior.
            "bollinger_position": _round(band_position, 3),
            "volatility_pct_per_candle": _round(volatility, 4),
            "volume_ratio_vs_avg20": _round(volume_ratio, 3),
        },
        "recent_candles": recent,
    }
