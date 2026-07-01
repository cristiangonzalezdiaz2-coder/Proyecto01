"""Cliente base para la API REST de MEXC con firma HMAC-SHA256."""
import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests


class MexcError(Exception):
    """Error devuelto por la API de MEXC o de red."""


class MexcBaseClient:
    """Maneja la firma y el envío de peticiones a la API de MEXC.

    Documentación oficial spot v3: https://mexcdevelop.github.io/apidocs/spot_v3_en/
    """

    BASE_URL = "https://api.mexc.com"
    RECV_WINDOW = 5000

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

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})

        if signed:
            if not self._api_key or not self._api_secret:
                raise MexcError("Se requieren API key y secret para peticiones firmadas.")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.RECV_WINDOW
            params["signature"] = self._sign(params)

        url = f"{self.BASE_URL}{path}"
        try:
            resp = self._session.request(
                method, url, params=params, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise MexcError(f"Error de red al llamar {path}: {exc}") from exc

        if resp.status_code != 200:
            raise MexcError(
                f"MEXC {resp.status_code} en {path}: {resp.text}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise MexcError(f"Respuesta no-JSON de {path}: {resp.text}") from exc

    # ------------------------------------------------------------------
    def get(self, path: str, params=None, signed: bool = False) -> Any:
        return self._request("GET", path, params, signed)

    def post(self, path: str, params=None, signed: bool = True) -> Any:
        return self._request("POST", path, params, signed)

    def delete(self, path: str, params=None, signed: bool = True) -> Any:
        return self._request("DELETE", path, params, signed)
