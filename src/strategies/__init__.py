"""Estrategias de trading."""
from .base import Signal, Strategy
from .ma_crossover import MACrossoverStrategy

# Registro de estrategias disponibles por nombre.
STRATEGIES = {
    "ma_crossover": MACrossoverStrategy,
}


def load_strategy(name: str, params: dict) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(
            f"Estrategia '{name}' desconocida. Disponibles: {list(STRATEGIES)}"
        )
    return STRATEGIES[name](**params)


__all__ = ["Signal", "Strategy", "MACrossoverStrategy", "load_strategy", "STRATEGIES"]
