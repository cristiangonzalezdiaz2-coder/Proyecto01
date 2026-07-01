"""Carga de configuración desde .env y config/config.yaml."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class RiskConfig:
    quote_per_trade: float = 20.0
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    max_daily_loss: float = 50.0
    max_open_positions: int = 1


@dataclass
class StrategyConfig:
    name: str = "ma_crossover"
    params: dict = field(default_factory=dict)


@dataclass
class AppConfig:
    api_key: str
    api_secret: str
    trading_mode: str  # "paper" o "live"
    symbol: str
    interval: str
    poll_seconds: int
    strategy: StrategyConfig
    risk: RiskConfig


def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    """Carga las credenciales del entorno y los parámetros del YAML."""
    load_dotenv()

    api_key = os.getenv("MEXC_API_KEY", "")
    api_secret = os.getenv("MEXC_API_SECRET", "")
    trading_mode = os.getenv("TRADING_MODE", "paper").lower()

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró {config_path}. Copia config/config.example.yaml a config/config.yaml."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    raw_strategy = raw.get("strategy", {})
    strategy = StrategyConfig(
        name=raw_strategy.get("name", "ma_crossover"),
        params={k: v for k, v in raw_strategy.items() if k != "name"},
    )

    risk = RiskConfig(**(raw.get("risk", {})))

    return AppConfig(
        api_key=api_key,
        api_secret=api_secret,
        trading_mode=trading_mode,
        symbol=raw.get("symbol", "BTCUSDT"),
        interval=raw.get("interval", "15m"),
        poll_seconds=int(raw.get("poll_seconds", 60)),
        strategy=strategy,
        risk=risk,
    )
