"""Cliente base para la API REST de MEXC con firma HMAC-SHA256.

Incluye reintentos con backoff exponencial ante rate limits (429/418).
Los errores de red y los 5xx solo se reintentan en peticiones GET: un POST
(p. ej. crear una orden) podría haberse aplicado aunque la respuesta se
perdiera, y reintentarlo a ciegas duplicaría la orden.
"""
import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from ..logger import get_logger

log = get_logger("mexc")


class MexcError(Exception):
    """Error devuelto por la API de MEXC o de red."""


class MexcBaseClient:
    """Maneja la firma y el envío de peticiones a la API de MEXC.

    Documentación oficial spot v3: https://mexcdevelop.github.io/apidocs/spot_v3_en/
    """

    BASE_URL = "https://api.mexc.com"
    RECV_WINDOW = 5000
    MAX_RETRIES = 3        # reintentos ante rate limit / errores transitorios
    BACKOFF_BASE = 1.0     # espera inicial en segundos (1, 2, 4, ...)

    def __init__(self, api_key: str = "", api_secret: str = "", timeout: int = 10):
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._session = requests.Session()
        if api_key:
            self._session.headers.update({"X-MEXC-APIKEY": api_key})
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    def _sign(self, params: dict[str, Any]) -> str:
        """Genera la firma HMAC-SHA256 sobre la query string."""
        query = urlencode(params)
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        if delay <= 0:
            delay = self.BACKOFF_BASE * (2 ** attempt)
        time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        base_params = dict(params or {})
        if signed and (not self._api_key or not self._api_secret):
            raise MexcError("Se requieren API key y secret para peticiones firmadas.")

        url = f"{self.BASE_URL}{path}"
        for attempt in range(self.MAX_RETRIES + 1):
            # La firma incluye el timestamp, así que se regenera en cada intento.
            req_params = dict(base_params)
            if signed:
                req_params["timestamp"] = int(time.time() * 1000)
                req_params["recvWindow"] = self.RECV_WINDOW
                req_params["signature"] = self._sign(req_params)

            try:
                resp = self._session.request(
                    method, url, params=req_params, timeout=self._timeout
                )
            except requests.RequestException as exc:
                # Red caída: solo es seguro reintentar peticiones GET.
                if method == "GET" and attempt < self.MAX_RETRIES:
                    log.warning("Error de red en %s (intento %d): %s. Reintentando...",
                                path, attempt + 1, exc)
                    self._backoff(attempt)
                    continue
                raise MexcError(f"Error de red al llamar {path}: {exc}") from exc

            if resp.status_code in (429, 418):  # rate limit / ban temporal
                if attempt < self.MAX_RETRIES:
                    log.warning("Rate limit de MEXC en %s (HTTP %d, intento %d). "
                                "Esperando antes de reintentar...",
                                path, resp.status_code, attempt + 1)
                    self._backoff(attempt, resp.headers.get("Retry-After"))
                    continue
                raise MexcError(
                    f"MEXC {resp.status_code} (rate limit) en {path} tras "
                    f"{self.MAX_RETRIES} reintentos: {resp.text}"
                )

            if resp.status_code >= 500 and method == "GET" and attempt < self.MAX_RETRIES:
                log.warning("MEXC %d en %s (intento %d). Reintentando...",
                            resp.status_code, path, attempt + 1)
                self._backoff(attempt)
                continue

            if resp.status_code != 200:
                raise MexcError(
                    f"MEXC {resp.status_code} en {path}: {resp.text}"
                )

            try:
                return resp.json()
            except ValueError as exc:
                raise MexcError(f"Respuesta no-JSON de {path}: {resp.text}") from exc

        raise MexcError(f"Agotados los reintentos al llamar {path}.")  # inalcanzable

    # ------------------------------------------------------------------
    def get(self, path: str, params=None, signed: bool = False) -> Any:
        return self._request("GET", path, params, signed)

    def post(self, path: str, params=None, signed: bool = True) -> Any:
        return self._request("POST", path, params, signed)

    def delete(self, path: str, params=None, signed: bool = True) -> Any:
        return self._request("DELETE", path, params, signed)
