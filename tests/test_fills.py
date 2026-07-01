"""Pruebas de la verificación de órdenes en modo live.

Cubre resolve_order_outcome (parseo de estado + fill, consulta con reintentos)
y que el motor:
  - construya la posición con el precio medio y la cantidad reales,
  - no registre posición si la compra terminó sin ejecutarse,
  - mantenga la posición abierta si la venta terminó sin ejecutarse,
  - calcule el PnL sobre lo realmente vendido en ejecuciones parciales.
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine, resolve_order_outcome


class FakeClient:
    """Cliente MEXC falso: sin red, con respuestas programables."""

    def __init__(self, api_key="", api_secret=""):
        self.order_response: dict = {}
        self.query_responses: list[dict] = []
        self.orders: list[dict] = []
        self.queries = 0

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.order_response

    def query_order(self, symbol, order_id):
        self.queries += 1
        if not self.query_responses:
            raise MexcError("orden no encontrada")
        return self.query_responses.pop(0)


# --------------------------- resolve_order_outcome ---------------------------
def test_fill_from_immediate_response():
    resp = {"orderId": "1", "status": "FILLED",
            "executedQty": "0.5", "cummulativeQuoteQty": "50.0"}
    outcome = resolve_order_outcome(None, "BTCUSDT", resp)
    assert outcome.filled and not outcome.failed
    assert (outcome.avg_price, outcome.executed_qty) == (100.0, 0.5)


def test_fill_polls_until_executed():
    client = FakeClient()
    client.query_responses = [
        {"status": "NEW", "executedQty": "0", "cummulativeQuoteQty": "0"},
        {"status": "FILLED", "executedQty": "2", "cummulativeQuoteQty": "40"},
    ]
    outcome = resolve_order_outcome(client, "BTCUSDT", {"orderId": "abc"}, delay=0)
    assert outcome.filled
    assert (outcome.avg_price, outcome.executed_qty) == (20.0, 2.0)


def test_unknown_outcome_without_order_id():
    outcome = resolve_order_outcome(None, "BTCUSDT", {})
    assert not outcome.filled and not outcome.failed


def test_unknown_outcome_if_never_executed():
    client = FakeClient()
    client.query_responses = [
        {"status": "NEW", "executedQty": "0", "cummulativeQuoteQty": "0"}
    ] * 5
    outcome = resolve_order_outcome(client, "BTCUSDT", {"orderId": "x"},
                                    attempts=3, delay=0)
    assert not outcome.filled and not outcome.failed


def test_outcome_survives_query_errors():
    client = FakeClient()  # query_order lanza MexcError siempre
    outcome = resolve_order_outcome(client, "BTCUSDT", {"orderId": "x"},
                                    attempts=2, delay=0)
    assert not outcome.filled and not outcome.failed


def test_failed_outcome_stops_polling():
    client = FakeClient()
    client.query_responses = [
        {"status": "CANCELED", "executedQty": "0", "cummulativeQuoteQty": "0"},
        {"status": "FILLED", "executedQty": "1", "cummulativeQuoteQty": "10"},
    ]
    outcome = resolve_order_outcome(client, "BTCUSDT", {"orderId": "x"}, delay=0)
    assert outcome.failed and not outcome.filled
    assert client.queries == 1  # tras el estado terminal no se sigue consultando


def test_partial_cancel_with_execution_is_partial_fill():
    resp = {"orderId": "1", "status": "PARTIALLY_CANCELED",
            "executedQty": "0.4", "cummulativeQuoteQty": "40.0"}
    outcome = resolve_order_outcome(None, "BTCUSDT", resp)
    assert outcome.filled and outcome.partial and not outcome.failed


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
        "orderId": "1", "status": "FILLED",
        "executedQty": "0.00049800", "cummulativeQuoteQty": "20.0",
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


def test_live_buy_rejected_opens_nothing(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "status": "REJECTED",
        "executedQty": "0", "cummulativeQuoteQty": "0",
    }
    assert eng._market_buy(40000.0) is None
    assert not eng.risk.open_positions
    assert not eng.store.fetch_open_positions()


def test_live_sell_uses_real_fill_for_pnl(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "status": "FILLED",
        "executedQty": "0.001", "cummulativeQuoteQty": "40.0",
    }
    pos = eng._market_buy(40000.0)  # fill exacto @ 40000

    # La venta se dispara a 41000 (vela), pero se ejecuta a 41500 reales.
    eng.client.order_response = {
        "orderId": "2", "status": "FILLED",
        "executedQty": "0.001", "cummulativeQuoteQty": "41.5",
    }
    eng._market_sell(pos, 41000.0, "take_profit")

    trade = eng.store.fetch_trades()[0]
    assert trade["exit_price"] == pytest.approx(41500.0)
    assert trade["pnl"] == pytest.approx((41500.0 - 40000.0) * 0.001)
    assert not eng.risk.open_positions


def test_live_sell_canceled_keeps_position_open(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "status": "FILLED",
        "executedQty": "0.001", "cummulativeQuoteQty": "40.0",
    }
    pos = eng._market_buy(40000.0)

    eng.client.order_response = {
        "orderId": "2", "status": "CANCELED",
        "executedQty": "0", "cummulativeQuoteQty": "0",
    }
    eng._market_sell(pos, 41000.0, "take_profit")

    # No se vendió nada: la posición sigue abierta y no hay trade registrado.
    assert eng.risk.open_positions == [pos]
    assert eng.store.fetch_trades() == []
    assert eng.risk.daily_pnl == 0.0


def test_live_sell_partial_computes_pnl_on_sold_qty(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_response = {
        "orderId": "1", "status": "FILLED",
        "executedQty": "0.001", "cummulativeQuoteQty": "40.0",
    }
    pos = eng._market_buy(40000.0)

    # Solo se vende el 60% (0.0006) a un precio medio de 41500.
    eng.client.order_response = {
        "orderId": "2", "status": "PARTIALLY_CANCELED",
        "executedQty": "0.0006", "cummulativeQuoteQty": "24.9",
    }
    eng._market_sell(pos, 41000.0, "take_profit")

    trade = eng.store.fetch_trades()[0]
    assert trade["exit_price"] == pytest.approx(41500.0)
    assert trade["quantity"] == pytest.approx(0.0006)
    assert trade["pnl"] == pytest.approx((41500.0 - 40000.0) * 0.0006)
    assert not eng.risk.open_positions
