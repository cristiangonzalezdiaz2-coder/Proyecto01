"""Notificador de Telegram.

Envía mensajes a un chat usando la Bot API de Telegram. Si no hay token o
chat_id configurados, el notificador queda deshabilitado silenciosamente (útil
en backtesting o si el usuario no quiere notificaciones).

Cómo obtener las credenciales:
  1. Habla con @BotFather en Telegram, crea un bot y copia el TOKEN.
  2. Escríbele algo a tu bot y visita:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     El "chat":{"id": ...} que aparece es tu TELEGRAM_CHAT_ID.
"""
import requests

from ..logger import get_logger

log = get_logger("telegram")


class TelegramNotifier:
    API_URL = "https://api.telegram.org"

    def __init__(self, token: str = "", chat_id: str = "", timeout: int = 10):
        self._token = token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout
        self.enabled = bool(self._token and self._chat_id)
        if not self.enabled:
            log.info("Notificaciones de Telegram deshabilitadas (sin token/chat_id).")

    def send(self, text: str) -> bool:
        """Envía un mensaje. Nunca lanza excepción: un fallo de notificación
        no debe tumbar el bot de trading."""
        if not self.enabled:
            return False
        url = f"{self.API_URL}/bot{self._token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                log.warning("Telegram respondió %s: %s", resp.status_code, resp.text)
                return False
            return True
        except requests.RequestException as exc:
            log.warning("No se pudo enviar notificación de Telegram: %s", exc)
            return False
