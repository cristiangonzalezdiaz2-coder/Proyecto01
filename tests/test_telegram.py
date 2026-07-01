"""Pruebas del notificador de Telegram (sin red real)."""
from src.notifications import TelegramNotifier


def test_disabled_without_credentials():
    n = TelegramNotifier(token="", chat_id="")
    assert n.enabled is False
    # send() no debe fallar aunque esté deshabilitado.
    assert n.send("hola") is False


def test_enabled_with_credentials():
    n = TelegramNotifier(token="123:abc", chat_id="999")
    assert n.enabled is True


def test_send_never_raises_on_network_error(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.RequestException("sin red")

    monkeypatch.setattr(requests, "post", boom)
    n = TelegramNotifier(token="123:abc", chat_id="999")
    # Un fallo de red no debe propagar excepción; devuelve False.
    assert n.send("mensaje") is False
