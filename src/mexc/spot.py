"""Endpoints de spot de MEXC construidos sobre el cliente base."""
from typing import Any

from .client import MexcBaseClient


class MexcSpotClient(MexcBaseClient):
    """Métodos de alto nivel para operar en el mercado spot de MEXC."""

    # -------------------- Públicos (sin firma) --------------------
    def ping(self) -> dict:
        """Comprueba conectividad con la API."""
        return self.get("/api/v3/ping")

    def server_time(self) -> dict:
        return self.get("/api/v3/time")

    def get_price(self, symbol: str) -> float:
        """Último precio de un símbolo, ej. 'BTCUSDT'."""
        data = self.get("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"])

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list[Any]]:
        """Devuelve velas OHLCV. Cada vela:
        [open_time, open, high, low, close, volume, close_time, quote_volume]
        """
        return self.get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def exchange_info(self, symbol: str) -> dict:
        """Reglas de trading del símbolo (precisión, mínimos, etc.)."""
        return self.get("/api/v3/exchangeInfo", {"symbol": symbol})

    # -------------------- Privados (firmados) --------------------
    def account(self) -> dict:
        """Información de la cuenta, incluidos los balances."""
        return self.get("/api/v3/account", signed=True)

    def get_balance(self, asset: str) -> float:
        """Balance libre de un activo concreto, ej. 'USDT'."""
        data = self.account()
        for bal in data.get("balances", []):
            if bal["asset"] == asset:
                return float(bal["free"])
        return 0.0

    def new_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: float | None = None,
        quote_order_qty: float | None = None,
        price: float | None = None,
    ) -> dict:
        """Crea una orden real en el mercado spot.

        side: 'BUY' o 'SELL'
        order_type: 'MARKET' o 'LIMIT'
        quantity: cantidad en el activo base (ej. BTC)
        quote_order_qty: para MARKET BUY, monto en la moneda cotizada (ej. USDT)
        price: requerido para órdenes LIMIT
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
        }
        if quantity is not None:
            params["quantity"] = quantity
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty
        if price is not None:
            params["price"] = price
        return self.post("/api/v3/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self.delete(
            "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    def open_orders(self, symbol: str) -> list:
        return self.get("/api/v3/openOrders", {"symbol": symbol}, signed=True)
