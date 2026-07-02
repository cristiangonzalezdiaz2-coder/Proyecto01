"""Pruebas del feed de precios WebSocket (parseo y frescura).

No se abre ninguna conexión real: se prueba el manejo de mensajes y la
regla de frescura (un precio más viejo que MAX_AGE no se usa, para que el
motor caiga al precio REST en vez de operar con datos rancios).
"""
import json

import pytest

import src.mexc.pricefeed as pricefeed_mod
from src.mexc.pricefeed import WebSocketPriceFeed


def _feed():
    return WebSocketPriceFeed(["BTCUSDT", "ETHUSDT"])


def test_parses_deals_message():
    feed = _feed()
    feed._handle_message(json.dumps({
        "c": "spot@public.deals.v3.api@BTCUSDT", "s": "BTCUSDT",
        "d": {"deals": [{"p": "40000.5", "v": "0.01"}, {"p": "40001.0", "v": "0.02"}]},
    }))
    assert feed.price("BTCUSDT") == pytest.approx(40001.0)  # la última operación
    assert feed.price("ETHUSDT") is None


def test_parses_ticker_style_message():
    feed = _feed()
    feed._handle_message(json.dumps({"s": "ETHUSDT", "d": {"p": "2500.25"}}))
    assert feed.price("ETHUSDT") == pytest.approx(2500.25)


def test_ignores_pongs_acks_and_garbage():
    feed = _feed()
    feed._handle_message(json.dumps({"id": 0, "code": 0, "msg": "PONG"}))
    feed._handle_message(json.dumps({"s": "BTCUSDT", "d": {"p": "no-numérico"}}))
    feed._handle_message(json.dumps({"s": "BTCUSDT", "d": {"p": "-5"}}))
    feed._handle_message("esto no es JSON {")
    feed._handle_message(json.dumps(["lista", "inesperada"]))
    assert feed.price("BTCUSDT") is None


def test_stale_price_returns_none(monkeypatch):
    feed = _feed()
    now = [1000.0]
    monkeypatch.setattr(pricefeed_mod.time, "monotonic", lambda: now[0])
    feed._handle_message(json.dumps({"s": "BTCUSDT", "d": {"p": "40000"}}))
    assert feed.price("BTCUSDT") == pytest.approx(40000.0)

    now[0] += WebSocketPriceFeed.MAX_AGE + 1  # el dato envejece
    assert feed.price("BTCUSDT") is None


def test_symbol_lookup_is_case_insensitive():
    feed = _feed()
    feed._handle_message(json.dumps({"s": "BTCUSDT", "d": {"p": "40000"}}))
    assert feed.price("btcusdt") == pytest.approx(40000.0)
