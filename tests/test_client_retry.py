"""Pruebas de los reintentos con backoff del cliente MEXC.

Reglas: los rate limits (429/418) se reintentan siempre (la petición fue
rechazada, no ejecutada); los errores de red y los 5xx solo se reintentan en
GET, porque un POST (crear una orden) podría haberse aplicado y reintentarlo
a ciegas duplicaría la orden.
"""
import pytest
import requests

import src.mexc.client as client_mod
from src.mexc.client import MexcBaseClient, MexcError


class FakeResponse:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.headers = headers or {}
        self.text = str(self._data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def request(self, method, url, params=None, timeout=None):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture()
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(client_mod.time, "sleep", recorded.append)
    return recorded


def _client(results) -> MexcBaseClient:
    c = MexcBaseClient()
    c._session = FakeSession(results)
    return c


def test_rate_limit_retried_then_succeeds(sleeps):
    c = _client([FakeResponse(429), FakeResponse(200, {"ok": 1})])
    assert c.get("/api/v3/ping") == {"ok": 1}
    assert c._session.calls == 2
    assert sleeps == [1.0]  # backoff base


def test_rate_limit_respects_retry_after(sleeps):
    c = _client([FakeResponse(429, headers={"Retry-After": "7"}),
                 FakeResponse(200, {})])
    c.get("/api/v3/ping")
    assert sleeps == [7.0]


def test_rate_limit_gives_up_after_max_retries(sleeps):
    c = _client([FakeResponse(429)] * 10)
    with pytest.raises(MexcError, match="rate limit"):
        c.get("/api/v3/ping")
    assert c._session.calls == MexcBaseClient.MAX_RETRIES + 1


def test_post_rate_limited_is_retried(sleeps):
    # Un 429 significa que la petición fue RECHAZADA: reintentar un POST es seguro.
    c = _client([FakeResponse(429), FakeResponse(200, {"orderId": "1"})])
    assert c.post("/api/v3/order", signed=False) == {"orderId": "1"}
    assert c._session.calls == 2


def test_post_network_error_not_retried(sleeps):
    # Tras un error de red la orden pudo haberse creado: reintentar duplicaría.
    c = _client([requests.ConnectionError("boom"), FakeResponse(200, {})])
    with pytest.raises(MexcError, match="red"):
        c.post("/api/v3/order", signed=False)
    assert c._session.calls == 1


def test_get_network_error_retried(sleeps):
    c = _client([requests.ConnectionError("boom"), FakeResponse(200, {"ok": 1})])
    assert c.get("/api/v3/ping") == {"ok": 1}
    assert c._session.calls == 2


def test_post_5xx_not_retried(sleeps):
    c = _client([FakeResponse(502), FakeResponse(200, {})])
    with pytest.raises(MexcError, match="502"):
        c.post("/api/v3/order", signed=False)
    assert c._session.calls == 1


def test_get_5xx_retried(sleeps):
    c = _client([FakeResponse(502), FakeResponse(200, {"ok": 1})])
    assert c.get("/api/v3/ping") == {"ok": 1}
    assert c._session.calls == 2
