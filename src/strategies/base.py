"""Interfaz base para las estrategias."""
from abc import ABC, abstractmethod
from enum import Enum

import numpy as np
import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(ABC):
    """Toda estrategia recibe un DataFrame de velas y devuelve una señal.

    El DataFrame tiene columnas: open, high, low, close, volume (float),
    indexado por tiempo de cierre.
    """

    name: str = "base"
    # False en estrategias que no pueden evaluarse vela a vela sobre el
    # histórico (p. ej. ai_agent: una llamada a la API por vela). El backtest
    # comparativo y las pruebas de vectorización las omiten.
    supports_backtest: bool = True

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        """Devuelve BUY, SELL o HOLD según las velas más recientes."""
        raise NotImplementedError

    def generate_signals(self, candles: pd.DataFrame) -> list[Signal]:
        """Señal para CADA vela del histórico, en una sola pasada.

        Contrato: el resultado en el índice i debe coincidir con
        generate_signal(candles.iloc[:i+1]). Los indicadores usados son
        causales (rolling/ewm solo miran hacia atrás), así que calcularlos
        una vez sobre todo el histórico da los mismos valores.

        Esta implementación por defecto usa ventanas crecientes (O(n²)) para
        que cualquier estrategia externa funcione; las integradas la
        sobreescriben con una versión vectorizada O(n)."""
        return [self.generate_signal(candles.iloc[: i + 1]) for i in range(len(candles))]


def crossings_to_signals(buy: pd.Series, sell: pd.Series, min_index: int) -> list[Signal]:
    """Convierte dos series booleanas de cruces en la lista de señales.

    `min_index` replica la guarda de historia mínima de generate_signal:
    todo índice anterior queda en HOLD. Si una vela marca compra y venta a la
    vez, gana BUY (mismo orden de comprobación que generate_signal)."""
    signals = [Signal.HOLD] * len(buy)
    for i in np.flatnonzero(buy.to_numpy(dtype=bool)):
        if i >= min_index:
            signals[i] = Signal.BUY
    for i in np.flatnonzero(sell.to_numpy(dtype=bool)):
        if i >= min_index and signals[i] is Signal.HOLD:
            signals[i] = Signal.SELL
    return signals


def compute_signals(strategy, candles: pd.DataFrame) -> list[Signal]:
    """Señales por vela para cualquier estrategia.

    Usa la versión vectorizada si el objeto la ofrece; si no (p. ej. una
    estrategia externa que solo define generate_signal), cae al bucle de
    ventanas crecientes."""
    gen = getattr(strategy, "generate_signals", None)
    if gen is not None:
        return gen(candles)
    return [strategy.generate_signal(candles.iloc[: i + 1]) for i in range(len(candles))]
