"""Pruebas de la sincronización de reloj con el servidor de MEXC.

Las peticiones firmadas llevan un timestamp con recvWindow de 5 s: si el
reloj local deriva más que eso, MEXC las rechaza todas. El cliente calcula el
desfase contra /api/v3/time antes de la primera petición firmada y lo aplica.
"""
import pytest

import src.mexc.client as client_mod
from src.mexc.client import MexcBaseClient


class FakeResponse:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.headers = headers or {}
        self.text = str(self._data)

    def json(self):
        return self._data


class RecordingSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def request(self, method, url, params=None, timeout=None):
        self.calls.append((method, url, dict(params or {})))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture()
def frozen_time(monkeypatch):
    monkeypatch.setattr(client_mod.time, "time", lambda: 1000.0)  # 1_000_000 ms
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)


def test_signed_request_syncs_clock_and_applies_offset(frozen_time):
    c = MexcBaseClient("k", "s")
    c._session = RecordingSession([
        FakeResponse(200, {"serverTime": 1_000_000 + 7000}),  # +7 s de desfase
        FakeResponse(200, {"ok": 1}),
    ])
    assert c.get("/api/v3/account", signed=True) == {"ok": 1}

    time_call, signed_call = c._session.calls
    assert time_call[1].endswith("/api/v3/time")
    assert signed_call[2]["timestamp"] == 1_000_000 + 7000


def test_sync_happens_only_once(frozen_time):
    c = MexcBaseClient("k", "s")
    c._session = RecordingSession([
        FakeResponse(200, {"serverTime": 1_005_000}),
        FakeResponse(200, {}),
        FakeResponse(200, {}),
    ])
    c.get("/a", signed=True)
    c.get("/b", signed=True)

    urls = [url for _, url, _ in c._session.calls]
    assert sum(url.endswith("/api/v3/time") for url in urls) == 1
    assert c._session.calls[-1][2]["timestamp"] == 1_005_000


def test_sync_failure_falls_back_to_local_clock(frozen_time):
    # El endpoint de hora agota sus reintentos: se sigue con el reloj local
    # y la sincronización se reintentará antes de la siguiente firmada.
    c = MexcBaseClient("k", "s")
    c._session = RecordingSession(
        [FakeResponse(500)] * (MexcBaseClient.MAX_RETRIES + 1)
        + [FakeResponse(200, {"ok": 1})]
    )
    assert c.get("/api/v3/account", signed=True) == {"ok": 1}
    assert c._session.calls[-1][2]["timestamp"] == 1_000_000
    assert c._time_synced is False  # se reintentará más adelante


def test_unsigned_requests_do_not_sync(frozen_time):
    c = MexcBaseClient()
    c._session = RecordingSession([FakeResponse(200, {"ok": 1})])
    assert c.get("/api/v3/ping") == {"ok": 1}
    assert len(c._session.calls) == 1  # sin llamada extra a /api/v3/time
