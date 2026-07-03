"""Estrategias de trading."""
from .ai_agent import AIAgentStrategy
from .base import Signal, Strategy, compute_signals
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
    "ai_agent": AIAgentStrategy,
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
    "compute_signals",
    "AIAgentStrategy",
    "MACrossoverStrategy",
    "RSIStrategy",
    "MACDStrategy",
    "BollingerStrategy",
    "load_strategy",
    "STRATEGIES",
]
