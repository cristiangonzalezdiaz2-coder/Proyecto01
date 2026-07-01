"""Pruebas del ajuste de cantidades al saldo libre real (live).

MEXC cobra la comisión de las compras MARKET en el activo comprado: tras
comprar 0.001 BTC el saldo libre queda en ~0.0009995. Colocar la orden TP o
vender por la cantidad completa sería rechazado por saldo insuficiente, así
que el motor capa la cantidad al saldo libre (truncado a la precisión).
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError, SymbolInfo
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine

INFO = SymbolInfo(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
                  base_precision=6, quote_precision=2, min_quote_amount=1.0,
                  min_quote_amount_market=1.0, min_base_size=0.0,
                  trading_allowed=True)

BUY_FILL = {"orderId": "buy1", "status": "FILLED",
            "executedQty": "0.001", "cummulativeQuoteQty": "40.0"}


class FakeClient:
    free_balance = 0.0        # se fija por subclase en cada test
    fail_balance = False

    def __init__(self, api_key="", api_secret=""):
        self.order_responses: list = []
        self.orders: list[dict] = []

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        return INFO

    def get_balance(self, asset):
        if type(self).fail_balance:
            raise MexcError("balance no disponible")
        return type(self).free_balance

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        resp = self.order_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def query_order(self, symbol, order_id):
        raise MexcError("orden no encontrada")

    def cancel_order(self, symbol, order_id):
        return {}


def _make_engine(tmp_path, monkeypatch, free, fail_balance=False) -> TradingEngine:
    client_cls = type("Client", (FakeClient,), {"free_balance": free,
                                                "fail_balance": fail_balance})
    monkeypatch.setattr(engine_mod, "MexcSpotClient", client_cls)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0))
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode="live",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def test_tp_order_capped_to_free_balance(tmp_path, monkeypatch):
    # Se compraron 0.001 pero la comisión dejó el saldo libre en 0.0009995.
    eng = _make_engine(tmp_path, monkeypatch, free=0.0009995)
    eng.client.order_responses = [dict(BUY_FILL), {"orderId": "tp1"}]
    pos = eng._market_buy(40000.0)

    tp_order = eng.client.orders[1]
    assert tp_order["order_type"] == "LIMIT"
    assert tp_order["quantity"] == pytest.approx(0.000999)  # truncado a 6 dec
    assert pos.tp_order_id == "tp1"


def test_tp_order_full_qty_when_balance_covers(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, free=0.002)
    eng.client.order_responses = [dict(BUY_FILL), {"orderId": "tp1"}]
    eng._market_buy(40000.0)
    assert eng.client.orders[1]["quantity"] == pytest.approx(0.001)


def test_market_sell_capped_to_free_balance(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, free=0.0009995)
    # La colocación del TP falla: la posición queda vigilada localmente.
    eng.client.order_responses = [dict(BUY_FILL), MexcError("sin saldo")]
    pos = eng._market_buy(40000.0)
    assert pos.tp_order_id is None

    eng.client.order_responses = [{"orderId": "s1", "status": "FILLED",
                                   "executedQty": "0.000999",
                                   "cummulativeQuoteQty": "38.961"}]
    eng._market_sell(pos, 39000.0, "stop_loss")

    assert eng.client.orders[-1]["side"] == "SELL"
    assert eng.client.orders[-1]["quantity"] == pytest.approx(0.000999)
    assert not eng.risk.open_positions


def test_balance_failure_falls_back_to_full_qty(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch, free=0.0, fail_balance=True)
    eng.client.order_responses = [dict(BUY_FILL), {"orderId": "tp1"}]
    eng._market_buy(40000.0)
    # Sin balance legible, se intenta con la cantidad completa (como antes).
    assert eng.client.orders[1]["quantity"] == pytest.approx(0.001)
