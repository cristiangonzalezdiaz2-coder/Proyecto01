"""Pruebas del agente de IA y de la estrategia ai_agent (sin red).

Se inyecta un cliente falso que imita la respuesta de la API de Anthropic,
así que ninguna prueba hace llamadas reales ni necesita ANTHROPIC_API_KEY.
"""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.ai.agent import AIAgent, AIDecision
from src.ai.context import build_market_context
from src.strategies import STRATEGIES, Signal, load_strategy
from src.strategies.ai_agent import AIAgentStrategy


# --------------------------- utilidades ---------------------------
def _candles(n=100, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({
        "open": np.roll(close, 1), "high": close * 1.005,
        "low": close * 0.995, "close": close,
        "volume": rng.uniform(10, 100, n),
    }, index=idx)


def _fake_response(payload: dict, stop_reason: str = "end_turn"):
    block = SimpleNamespace(type="text", text=json.dumps(payload))
    return SimpleNamespace(content=[block], stop_reason=stop_reason)


class FakeClient:
    """Imita client.messages.create devolviendo respuestas predefinidas."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _agent(*responses) -> tuple[AIAgent, FakeClient]:
    client = FakeClient(responses)
    return AIAgent(client=client), client


DECISION = {"signal": "BUY", "confidence": 0.8,
            "reasoning": "Confluencia alcista.", "key_risks": ["volatilidad"]}


# --------------------------- contexto ---------------------------
def test_context_is_json_serializable_and_complete():
    ctx = build_market_context("BTCUSDT", "15m", _candles())
    json.dumps(ctx)  # no debe lanzar
    assert ctx["symbol"] == "BTCUSDT"
    assert ctx["price"] > 0
    assert len(ctx["recent_candles"]) == 20
    ind = ctx["indicators"]
    assert 0 <= ind["rsi_14"] <= 100
    assert ind["sma_20"] is not None


def test_context_short_history_has_nulls_not_errors():
    ctx = build_market_context("BTCUSDT", "15m", _candles(n=10))
    json.dumps(ctx)
    assert ctx["indicators"]["sma_50"] is None
    assert ctx["returns_pct"]["50_velas"] is None


# --------------------------- agente ---------------------------
def test_agent_parses_valid_decision():
    agent, client = _agent(_fake_response(DECISION))
    decision = agent.decide({"symbol": "BTCUSDT"})
    assert decision.signal == "BUY"
    assert decision.confidence == 0.8
    assert decision.key_risks == ["volatilidad"]
    # La petición usa salida estructurada con el esquema de decisión.
    assert client.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_agent_clamps_out_of_range_confidence():
    agent, _ = _agent(_fake_response({**DECISION, "confidence": 3.5}))
    assert agent.decide({}).confidence == 1.0


def test_agent_holds_on_api_error():
    agent, _ = _agent(RuntimeError("API caída"))
    assert agent.decide({}).signal == "HOLD"


def test_agent_holds_on_refusal():
    agent, _ = _agent(_fake_response(DECISION, stop_reason="refusal"))
    assert agent.decide({}).signal == "HOLD"


def test_agent_holds_on_invalid_json():
    block = SimpleNamespace(type="text", text="esto no es JSON")
    response = SimpleNamespace(content=[block], stop_reason="end_turn")
    agent, _ = _agent(response)
    assert agent.decide({}).signal == "HOLD"


def test_agent_holds_on_unknown_signal():
    agent, _ = _agent(_fake_response({**DECISION, "signal": "SHORT"}))
    assert agent.decide({}).signal == "HOLD"


def test_agent_without_key_or_package_holds(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = AIAgent()  # sin cliente inyectado
    assert agent.decide({}).signal == "HOLD"


# --------------------------- estrategia ---------------------------
def _strategy(*responses, **kwargs) -> tuple[AIAgentStrategy, FakeClient]:
    client = FakeClient(responses)
    strat = AIAgentStrategy(symbol="BTCUSDT", interval="15m",
                            agent=AIAgent(client=client), **kwargs)
    return strat, client


def test_strategy_registered():
    assert "ai_agent" in STRATEGIES
    assert load_strategy("ai_agent", {}) is not None
    assert STRATEGIES["ai_agent"].supports_backtest is False


def test_strategy_translates_decision_to_signal():
    strat, _ = _strategy(_fake_response(DECISION))
    assert strat.generate_signal(_candles()) is Signal.BUY
    assert strat.last_decision.reasoning == "Confluencia alcista."


def test_strategy_low_confidence_becomes_hold():
    strat, _ = _strategy(_fake_response({**DECISION, "confidence": 0.4}))
    assert strat.generate_signal(_candles()) is Signal.HOLD


def test_strategy_caches_by_candle():
    # Misma vela final -> una sola llamada a la API aunque se consulte 3 veces.
    strat, client = _strategy(_fake_response(DECISION))
    df = _candles()
    for _ in range(3):
        assert strat.generate_signal(df) is Signal.BUY
    assert len(client.calls) == 1


def test_strategy_new_candle_triggers_new_call():
    sell = {**DECISION, "signal": "SELL", "confidence": 0.9}
    strat, client = _strategy(_fake_response(DECISION), _fake_response(sell))
    df = _candles(n=101)
    assert strat.generate_signal(df.iloc[:-1]) is Signal.BUY
    assert strat.generate_signal(df) is Signal.SELL
    assert len(client.calls) == 2


def test_strategy_holds_below_min_candles():
    strat, client = _strategy()
    assert strat.generate_signal(_candles(n=30)) is Signal.HOLD
    assert client.calls == []  # ni siquiera consulta a la API


def test_strategy_rejects_mass_backtest():
    strat, _ = _strategy()
    with pytest.raises(NotImplementedError):
        strat.generate_signals(_candles())


def test_strategy_invalid_min_confidence():
    with pytest.raises(ValueError):
        AIAgentStrategy(min_confidence=1.5)
