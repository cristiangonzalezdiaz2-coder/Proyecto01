"""Interfaz base para las estrategias."""
from abc import ABC, abstractmethod
from enum import Enum

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

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        """Devuelve BUY, SELL o HOLD según las velas más recientes."""
        raise NotImplementedError
