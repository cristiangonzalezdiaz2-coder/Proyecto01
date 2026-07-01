"""Pruebas del parseo y ajuste de precisión por símbolo (sin red)."""
from src.mexc import SymbolInfo

# Respuesta de exchangeInfo similar a la real de MEXC para BTCUSDT.
SAMPLE = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "ENABLED",
            "baseAsset": "BTC",
            "baseAssetPrecision": 6,
            "quoteAsset": "USDT",
            "quotePrecision": 2,
            "quoteAssetPrecision": 2,
            "isSpotTradingAllowed": True,
            "quoteAmountPrecision": "5",
            "quoteAmountPrecisionMarket": "5",
            "baseSizePrecision": "0.000001",
        }
    ]
}


def _info():
    return SymbolInfo.from_exchange_info(SAMPLE, "BTCUSDT")


def test_parse_fields():
    info = _info()
    assert info.base_precision == 6
    assert info.quote_precision == 2
    assert info.min_quote_amount_market == 5.0
    assert info.base_asset == "BTC"
    assert info.trading_allowed is True


def test_floor_quantity_truncates():
    info = _info()
    # 6 decimales, truncando (nunca redondea hacia arriba).
    assert info.floor_quantity(0.00012399) == 0.000123
    assert info.floor_quantity(0.0000009) == 0.0  # por debajo de 1e-6


def test_round_price():
    info = _info()
    assert info.round_price(65000.126) == 65000.13
    assert info.round_price(65000.124) == 65000.12


def test_check_market_buy_below_min():
    info = _info()
    amount, err = info.check_market_buy(3.0)  # < 5 USDT mínimo
    assert err is not None
    assert "mínimo" in err


def test_check_market_buy_ok():
    info = _info()
    amount, err = info.check_market_buy(20.0)
    assert err is None
    assert amount == 20.0


def test_check_sell_qty_below_min():
    info = _info()
    _, err = info.check_sell_qty(0.0000005)  # < baseSizePrecision (1e-6)
    assert err is not None


def test_unknown_symbol_raises():
    try:
        SymbolInfo.from_exchange_info(SAMPLE, "ETHUSDT")
        assert False
    except ValueError:
        pass


def test_trading_not_allowed():
    data = {"symbols": [dict(SAMPLE["symbols"][0], status="DISABLED",
                             isSpotTradingAllowed=False)]}
    info = SymbolInfo.from_exchange_info(data, "BTCUSDT")
    assert info.trading_allowed is False
    _, err = info.check_market_buy(20.0)
    assert err is not None
