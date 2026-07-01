"""Carga de configuración desde .env y config/config.yaml.

Admite dos formatos en el YAML:
  - Un solo bot: campos symbol/interval/strategy/risk en la raíz (compatibilidad).
  - Varios bots: una lista `bots:`, cada uno con su símbolo, estrategia y riesgo.
    Lo que no se especifique por bot hereda los valores de la raíz.
"""
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
    # Comisión del exchange por lado, como fracción (0.0005 = 0.05%, la tarifa
    # taker de MEXC spot). Se descuenta del PnL de cada operación (compra y
    # venta) en live, paper y backtest. Pon 0 para ignorar comisiones.
    fee_pct: float = 0.0005
    # Trailing stop (opcional): si es > 0, el stop-loss sube siguiendo al
    # precio a esta distancia (0.015 = 1.5% por debajo del máximo alcanzado).
    # Nunca baja del stop inicial y asegura beneficios cuando el precio
    # avanza. 0 = desactivado (stop fijo).
    trailing_stop_pct: float = 0.0


@dataclass
class StrategyConfig:
    name: str = "ma_crossover"
    params: dict = field(default_factory=dict)


@dataclass
class BotConfig:
    """Configuración de un bot individual (un par + una estrategia)."""
    name: str
    symbol: str
    interval: str
    poll_seconds: int
    strategy: StrategyConfig
    risk: RiskConfig


@dataclass
class GlobalRiskConfig:
    """Límites de riesgo COMPARTIDOS entre todos los bots.

    Un valor de 0 en cualquier límite significa "sin límite" en esa dimensión.
    `enabled` es True solo si el YAML incluye una sección `global_risk`.
    """
    enabled: bool = False
    max_total_exposure: float = 0.0    # capital máx. comprometido a la vez (todos los bots)
    max_daily_loss: float = 0.0        # pérdida diaria máxima combinada
    max_open_positions: int = 0        # posiciones abiertas simultáneas en total


@dataclass
class AppConfig:
    api_key: str
    api_secret: str
    trading_mode: str  # "paper" o "live"
    bots: list[BotConfig]
    global_risk: GlobalRiskConfig = field(default_factory=GlobalRiskConfig)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    db_path: str = "data/bot.db"

    # ---- Accesos de compatibilidad (apuntan al primer bot) ----
    @property
    def symbol(self) -> str:
        return self.bots[0].symbol

    @property
    def interval(self) -> str:
        return self.bots[0].interval

    @property
    def poll_seconds(self) -> int:
        return self.bots[0].poll_seconds

    @property
    def strategy(self) -> StrategyConfig:
        return self.bots[0].strategy

    @property
    def risk(self) -> RiskConfig:
        return self.bots[0].risk


def _parse_strategy(raw_strategy: dict, default: StrategyConfig) -> StrategyConfig:
    if not raw_strategy:
        return default
    return StrategyConfig(
        name=raw_strategy.get("name", default.name),
        params={k: v for k, v in raw_strategy.items() if k != "name"},
    )


def _bot_name(raw: dict, index: int) -> str:
    return str(raw.get("name") or f"{raw.get('symbol', 'bot')}-{raw.get('strategy', {}).get('name', index)}")


def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    """Carga las credenciales del entorno y los parámetros del YAML."""
    load_dotenv()

    api_key = os.getenv("MEXC_API_KEY", "")
    api_secret = os.getenv("MEXC_API_SECRET", "")
    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró {config_path}. Copia config/config.example.yaml a config/config.yaml."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    # Valores por defecto de la raíz (heredados por cada bot).
    default_interval = raw.get("interval", "15m")
    default_poll = int(raw.get("poll_seconds", 60))
    default_strategy = _parse_strategy(raw.get("strategy", {}), StrategyConfig())
    default_risk = RiskConfig(**(raw.get("risk", {})))

    bots: list[BotConfig] = []
    raw_bots = raw.get("bots")
    if raw_bots:
        names_seen: set[str] = set()
        for i, rb in enumerate(raw_bots):
            name = _bot_name(rb, i)
            # Evitar nombres duplicados (rompería la separación en la BD).
            base, n = name, 2
            while name in names_seen:
                name = f"{base}-{n}"
                n += 1
            names_seen.add(name)
            bots.append(BotConfig(
                name=name,
                symbol=rb.get("symbol", "BTCUSDT"),
                interval=rb.get("interval", default_interval),
                poll_seconds=int(rb.get("poll_seconds", default_poll)),
                strategy=_parse_strategy(rb.get("strategy", {}), default_strategy),
                risk=RiskConfig(**{**default_risk.__dict__, **rb.get("risk", {})}),
            ))
    else:
        # Formato antiguo: un solo bot desde la raíz.
        bots.append(BotConfig(
            name=raw.get("name", raw.get("symbol", "BTCUSDT")),
            symbol=raw.get("symbol", "BTCUSDT"),
            interval=default_interval,
            poll_seconds=default_poll,
            strategy=default_strategy,
            risk=default_risk,
        ))

    raw_global = raw.get("global_risk")
    if raw_global:
        global_risk = GlobalRiskConfig(
            enabled=True,
            max_total_exposure=float(raw_global.get("max_total_exposure", 0) or 0),
            max_daily_loss=float(raw_global.get("max_daily_loss", 0) or 0),
            max_open_positions=int(raw_global.get("max_open_positions", 0) or 0),
        )
    else:
        global_risk = GlobalRiskConfig()

    return AppConfig(
        api_key=api_key,
        api_secret=api_secret,
        trading_mode=trading_mode,
        bots=bots,
        global_risk=global_risk,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        db_path=raw.get("db_path", "data/bot.db"),
    )
