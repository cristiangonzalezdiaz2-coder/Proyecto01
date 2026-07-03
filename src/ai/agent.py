"""Agente de IA que decide señales de trading usando Claude (Anthropic).

El agente recibe el contexto de mercado (ver context.py), se lo pasa a
Claude y obtiene una decisión estructurada y validada:

    {"signal": "BUY"|"SELL"|"HOLD", "confidence": 0..1, "reasoning": "..."}

La salida usa el modo de salidas estructuradas de la API (json_schema),
así que siempre es JSON válido con esos campos. Cualquier fallo (API caída,
sin API key, respuesta rechazada) degrada a HOLD: el agente nunca debe
tumbar el bot ni forzar una operación por un error de infraestructura.

Requiere el paquete `anthropic` y la variable de entorno ANTHROPIC_API_KEY.
"""
import json
import os
from dataclasses import dataclass

from ..logger import get_logger

log = get_logger("ai")

DEFAULT_MODEL = "claude-opus-4-8"

# Esquema de la decisión. Las salidas estructuradas no admiten minimum/
# maximum numéricos, así que el rango de confidence se valida en código.
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"],
            "description": "Acción a tomar sobre el par en spot (solo largos).",
        },
        "confidence": {
            "type": "number",
            "description": "Confianza en la decisión, de 0.0 (nula) a 1.0 (máxima).",
        },
        "reasoning": {
            "type": "string",
            "description": "Justificación breve (2-4 frases) basada en los datos recibidos.",
        },
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Riesgos principales que podrían invalidar la decisión.",
        },
    },
    "required": ["signal", "confidence", "reasoning", "key_risks"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
Eres un agente de análisis técnico para un bot de trading spot de criptomonedas.
El bot solo opera en largo: BUY abre (o mantiene la intención de abrir) una
posición, SELL cierra la posición si existe, HOLD no hace nada. El bot ya
aplica stop-loss, take-profit y límites de riesgo por su cuenta; tu trabajo es
únicamente evaluar si el contexto técnico favorece entrar, salir o esperar.

Recibirás un JSON con el estado del mercado: precio, retornos recientes,
indicadores (RSI, MACD, medias móviles, bandas de Bollinger, volatilidad,
volumen relativo) y las últimas velas OHLCV.

Criterios:
- Emite BUY solo con confluencia clara de varias señales alcistas (tendencia,
  momentum y volumen coherentes). Una sola señal aislada no basta.
- Emite SELL cuando el contexto se deteriore de forma clara (pérdida de
  momentum, ruptura de soportes, sobrecompra con divergencia, etc.).
- Ante ambigüedad, datos insuficientes o mercado lateral sin dirección: HOLD.
  Es preferible perder una oportunidad que forzar una operación dudosa.
- La confianza debe reflejar honestamente la calidad de la evidencia; no
  infles el número. Usa >0.8 solo con confluencia excepcional.
- Básate exclusivamente en los datos recibidos; no inventes noticias ni
  eventos externos.
"""


@dataclass
class AIDecision:
    """Decisión del agente ya validada."""
    signal: str  # "BUY", "SELL" o "HOLD"
    confidence: float  # 0.0 a 1.0
    reasoning: str
    key_risks: list[str]

    HOLD = None  # se define tras la clase


AIDecision.HOLD = AIDecision(
    signal="HOLD", confidence=0.0, reasoning="Sin decisión del agente.", key_risks=[]
)


class AIAgent:
    """Cliente del agente: una llamada a Claude por consulta.

    `client` se puede inyectar (tests); si es None se crea perezosamente un
    anthropic.Anthropic(), que toma la API key de ANTHROPIC_API_KEY.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 2000, client=None):
        self.model = model
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "Falta el paquete 'anthropic'. Instálalo con: pip install anthropic"
                ) from exc
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "Falta ANTHROPIC_API_KEY en el entorno (.env) para usar el agente de IA."
                )
            self._client = anthropic.Anthropic()
        return self._client

    def decide(self, market_context: dict) -> AIDecision:
        """Consulta a Claude y devuelve la decisión. Nunca lanza: ante error, HOLD."""
        try:
            client = self._get_client()
        except RuntimeError as exc:
            log.error("Agente IA no disponible: %s", exc)
            return AIDecision.HOLD

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                output_config={"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Analiza el siguiente estado del mercado y decide:\n"
                            + json.dumps(market_context, ensure_ascii=False)
                        ),
                    }
                ],
            )
        except Exception as exc:  # errores de red/API: el bot debe seguir vivo
            log.warning("Error consultando al agente IA: %s", exc)
            return AIDecision.HOLD

        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("El agente IA rechazó la consulta (refusal); se mantiene HOLD.")
            return AIDecision.HOLD
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.warning("Respuesta del agente IA truncada (max_tokens); se mantiene HOLD.")
            return AIDecision.HOLD

        return self._parse(response)

    def _parse(self, response) -> AIDecision:
        text = next(
            (block.text for block in response.content if getattr(block, "type", "") == "text"),
            "",
        )
        try:
            data = json.loads(text)
            signal = str(data["signal"]).upper()
            if signal not in ("BUY", "SELL", "HOLD"):
                raise ValueError(f"señal desconocida: {signal!r}")
            confidence = min(1.0, max(0.0, float(data["confidence"])))
            reasoning = str(data.get("reasoning", ""))
            key_risks = [str(r) for r in data.get("key_risks", [])]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Respuesta del agente IA no interpretable (%s): %.200s", exc, text)
            return AIDecision.HOLD
        return AIDecision(signal=signal, confidence=confidence,
                          reasoning=reasoning, key_risks=key_risks)
