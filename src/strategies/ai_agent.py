"""Estrategia ai_agent — decisiones tomadas por un agente de IA (Claude).

En cada vela cerrada nueva, la estrategia resume el mercado (indicadores +
últimas velas), se lo envía al agente y convierte su decisión en la señal
BUY/SELL/HOLD que consume el motor. Igual que el resto de estrategias, el
riesgo real (stop-loss, take-profit, sizing, límites diarios) lo sigue
aplicando el motor: el agente solo decide la dirección.

Protecciones incorporadas:
- Caché por vela: aunque el motor haga poll cada minuto, solo se llama a la
  API una vez por vela cerrada (el resto de polls reutilizan la decisión).
- Umbral de confianza: señales con confianza < min_confidence se degradan
  a HOLD.
- Cualquier error del agente (API, red, credenciales) produce HOLD; el bot
  nunca se detiene por un fallo de la IA.

El backtest masivo está deshabilitado a propósito: evaluar el histórico
completo haría una llamada a la API por vela (coste y lentitud). Valida
primero la estrategia en modo paper.
"""
import pandas as pd

from ..logger import get_logger
from .base import Signal, Strategy

log = get_logger("ai")


class AIAgentStrategy(Strategy):
    name = "ai_agent"
    supports_backtest = False

    def __init__(
        self,
        symbol: str = "",
        interval: str = "",
        model: str = "",
        min_confidence: float = 0.6,
        min_candles: int = 60,
        recent_candles: int = 20,
        agent=None,
    ):
        # Import perezoso: src.ai importa indicadores de src.strategies, así
        # que importarlo aquí arriba crearía un ciclo con el __init__ del
        # paquete de estrategias.
        from ..ai.agent import AIAgent, DEFAULT_MODEL

        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence debe estar entre 0 y 1.")
        self.symbol = symbol
        self.interval = interval
        self.min_confidence = min_confidence
        self.min_candles = min_candles
        self.recent_candles = recent_candles
        self.agent = agent or AIAgent(model=model or DEFAULT_MODEL)
        # Caché de la última decisión, indexada por la vela que la produjo.
        self._last_candle_ts = None
        self._last_signal = Signal.HOLD
        self.last_decision = None

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.min_candles:
            return Signal.HOLD

        candle_ts = candles.index[-1]
        if candle_ts == self._last_candle_ts:
            return self._last_signal

        from ..ai.context import build_market_context

        context = build_market_context(
            self.symbol, self.interval, candles, recent_candles=self.recent_candles
        )
        decision = self.agent.decide(context)
        self.last_decision = decision

        signal = Signal(decision.signal)
        if signal is not Signal.HOLD and decision.confidence < self.min_confidence:
            log.info(
                "Agente IA propuso %s con confianza %.2f < %.2f; se degrada a HOLD. Motivo: %s",
                decision.signal, decision.confidence, self.min_confidence, decision.reasoning,
            )
            signal = Signal.HOLD
        elif signal is not Signal.HOLD:
            log.info(
                "Agente IA: %s (confianza %.2f). Motivo: %s | Riesgos: %s",
                decision.signal, decision.confidence, decision.reasoning,
                "; ".join(decision.key_risks) or "-",
            )

        self._last_candle_ts = candle_ts
        self._last_signal = signal
        return signal

    def generate_signals(self, candles: pd.DataFrame) -> list[Signal]:
        raise NotImplementedError(
            "La estrategia ai_agent no soporta backtest masivo: haría una llamada "
            "a la API de Anthropic por cada vela del histórico (coste y lentitud). "
            "Valídala en modo paper, o backtestea con las estrategias técnicas."
        )
