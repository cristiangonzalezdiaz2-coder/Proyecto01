"""Pruebas del uso de datos reales de ejecución (fills) en modo live.

Cubre resolve_order_fill (parseo de la respuesta y consulta con reintentos)
y que el motor construya la posición con el precio medio y la cantidad
realmente ejecutados, calculando el PnL con el precio real de salida.
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine, resolve_order_fill


class FakeClient:
    """Cliente MEXC falso: sin red, con respuestas programables."""

    def __init__(self, api_key="", api_secret=""):
        self.order_response: dict = {}
        self.query_responses: list[dict] = []
        self.orders: list[dict] = []

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.order_response

    def query_order(self, symbol, order_id):
        if not self.query_responses:
            raise MexcError("orden no encontrada")
        return self.query_responses.pop(0)


# --------------------------- resolve_order_fill ---------------------------
def test_fill_from_immediate_response():
    resp = {"orderId": "1", "executedQty": "0.5", "cummulativeQuoteQty": "50.0"}
    assert resolve_order_fill(None, "BTCUSDT", resp) == (100.0, 0.5)


def test_fill_polls_until_executed():
    client = FakeClient()
    client.query_responses = [
        {"executedQty": "0", "cummulativeQuoteQty": "0"},
        {"executedQty": "2", "cummulativeQuoteQty": "40"},
    ]
    fill = resolve_order_fill(client, "BTCUSDT", {"orderId": "abc"}, delay=0)
    assert fill == (20.0, 2.0)


def test_fill_none_without_order_id():
    assert resolve_order_fill(None, "BTCUSDT", {}) is None


def test_fill_none_if_never_executed():
    client = FakeClient()
    client.query_responses = [{"executedQty": "0", "cummulativeQuoteQty": "0"}] * 5
    fill = resolve_order_fill(client, "BTCUSDT", {"orderId": "x"}, attempts=3, delay=0)
    assert fill is None


def test_fill_survives_query_errors():
    client = FakeClient()  # query_order lanza MexcError siempre
    fill = resolve_order_fill(client, "BTCUSDT", {"orderId": "x"}, attempts=2, delay=0)
    assert fill is None


# --------------------------- Motor en modo live ---------------------------
def _make_engine(tmp_path, monkeypatch) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(
        name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
        strategy=StrategyConfig(), risk=RiskConfig(quote_per_trade=20.0),
    )
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode="live",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def test_live_buy_uses_real_fill(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "executedQty": "0.00049800", "cummulativeQuoteQty": "20.0",
    }
    pos = eng._market_buy(40000.0)  # precio de la vela (estimación)
    fill_price = 20.0 / 0.000498
    assert pos.quantity == pytest.approx(0.000498)
    assert pos.entry_price == pytest.approx(fill_price)
    # SL/TP recalculados desde el precio real de compra, no desde la vela.
    assert pos.stop_loss == pytest.approx(fill_price * 0.98)
    assert pos.take_profit == pytest.approx(fill_price * 1.04)


def test_live_buy_falls_back_to_estimate(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {}  # sin datos de ejecución ni orderId
    pos = eng._market_buy(40000.0)
    assert pos.entry_price == pytest.approx(40000.0)
    assert pos.quantity == pytest.approx(20.0 / 40000.0)


def test_live_sell_uses_real_fill_for_pnl(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "executedQty": "0.001", "cummulativeQuoteQty": "40.0",
    }
    pos = eng._market_buy(40000.0)  # fill exacto @ 40000

    # La venta se dispara a 41000 (vela), pero se ejecuta a 41500 reales.
    eng.client.order_response = {
        "orderId": "2", "executedQty": "0.001", "cummulativeQuoteQty": "41.5",
    }
    eng._market_sell(pos, 41000.0, "take_profit")

    trade = eng.store.fetch_trades()[0]
    assert trade["exit_price"] == pytest.approx(41500.0)
    assert trade["pnl"] == pytest.approx((41500.0 - 40000.0) * 0.001)
    assert not eng.risk.open_positions
