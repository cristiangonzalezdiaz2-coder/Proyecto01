"""Precisión y mínimos de trading por símbolo (desde /api/v3/exchangeInfo).

MEXC define, para cada par, cuántos decimales admite la cantidad (activo base)
y el precio (moneda cotizada), así como el importe mínimo de orden (notional).
Enviar una orden que no respete estas reglas hace que MEXC la rechace, por eso
redondeamos SIEMPRE la cantidad hacia abajo (truncando) para no exceder el
balance disponible, y validamos el mínimo antes de operar.
"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


def _floor_to(value: float, decimals: int) -> float:
    """Trunca `value` a `decimals` decimales (redondeo hacia abajo)."""
    if decimals < 0:
        decimals = 0
    q = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_DOWN))


def _round_to(value: float, decimals: int) -> float:
    if decimals < 0:
        decimals = 0
    q = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


@dataclass
class SymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    base_precision: int          # decimales de la cantidad (activo base)
    quote_precision: int         # decimales del precio (moneda cotizada)
    min_quote_amount: float      # notional mínimo para órdenes LIMIT
    min_quote_amount_market: float  # notional mínimo para órdenes MARKET
    min_base_size: float         # cantidad base mínima
    trading_allowed: bool

    # ---------------------------------------------------------------
    @classmethod
    def from_exchange_info(cls, data: dict, symbol: str) -> "SymbolInfo":
        """Construye a partir de la respuesta de /api/v3/exchangeInfo."""
        symbols = data.get("symbols", [])
        entry = next((s for s in symbols if s.get("symbol") == symbol), None)
        if entry is None:
            raise ValueError(f"El símbolo {symbol} no está en exchangeInfo.")

        quote_precision = entry.get("quotePrecision", entry.get("quoteAssetPrecision", 8))
        min_quote = float(entry.get("quoteAmountPrecision", 0) or 0)
        min_quote_market = float(
            entry.get("quoteAmountPrecisionMarket", entry.get("quoteAmountPrecision", 0)) or 0
        )
        status = str(entry.get("status", "")).upper()
        trading_allowed = bool(
            entry.get("isSpotTradingAllowed", True)
        ) and status in ("ENABLED", "1", "TRADING", "")

        return cls(
            symbol=symbol,
            base_asset=entry.get("baseAsset", ""),
            quote_asset=entry.get("quoteAsset", ""),
            base_precision=int(entry.get("baseAssetPrecision", 8)),
            quote_precision=int(quote_precision),
            min_quote_amount=min_quote,
            min_quote_amount_market=min_quote_market,
            min_base_size=float(entry.get("baseSizePrecision", 0) or 0),
            trading_allowed=trading_allowed,
        )

    # ---------------------------------------------------------------
    def floor_quantity(self, qty: float) -> float:
        """Trunca la cantidad base a la precisión permitida."""
        return _floor_to(qty, self.base_precision)

    def round_price(self, price: float) -> float:
        """Redondea el precio a la precisión de la moneda cotizada."""
        return _round_to(price, self.quote_precision)

    def floor_quote(self, amount: float) -> float:
        """Trunca un importe en moneda cotizada a su precisión."""
        return _floor_to(amount, self.quote_precision)

    # ---------------------------------------------------------------
    def check_market_buy(self, quote_amount: float) -> tuple[float, str | None]:
        """Ajusta y valida el importe (quote) de una compra MARKET.

        Devuelve (importe_ajustado, error). Si error no es None, no se debe operar.
        """
        amount = self.floor_quote(quote_amount)
        if not self.trading_allowed:
            return amount, f"El par {self.symbol} no admite trading spot ahora mismo."
        if self.min_quote_amount_market and amount < self.min_quote_amount_market:
            return amount, (
                f"Importe {amount} < mínimo de mercado {self.min_quote_amount_market} "
                f"{self.quote_asset}."
            )
        return amount, None

    def check_sell_qty(self, qty: float) -> tuple[float, str | None]:
        """Ajusta y valida la cantidad base de una venta MARKET."""
        adjusted = self.floor_quantity(qty)
        if adjusted <= 0:
            return adjusted, "La cantidad redondeada es 0."
        if self.min_base_size and adjusted < self.min_base_size:
            return adjusted, (
                f"Cantidad {adjusted} < mínima {self.min_base_size} {self.base_asset}."
            )
        return adjusted, None
