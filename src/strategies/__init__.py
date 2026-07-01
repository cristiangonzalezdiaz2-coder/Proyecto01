"""Estrategias de trading."""
from .base import Signal, Strategy
from .bollinger import BollingerStrategy
from .macd import MACDStrategy
from .ma_crossover import MACrossoverStrategy
from .rsi import RSIStrategy

# Registro de estrategias disponibles por nombre.
STRATEGIES = {
    "ma_crossover": MACrossoverStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
}


def load_strategy(name: str, params: dict) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(
            f"Estrategia '{name}' desconocida. Disponibles: {list(STRATEGIES)}"
        )
    return STRATEGIES[name](**params)


__all__ = [
    "Signal",
    "Strategy",
    "MACrossoverStrategy",
    "RSIStrategy",
    "MACDStrategy",
    "BollingerStrategy",
    "load_strategy",
    "STRATEGIES",
]
