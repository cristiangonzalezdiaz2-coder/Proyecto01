"""Pruebas del take-profit colocado como orden LIMIT en el exchange (live).

MEXC spot v3 no admite órdenes stop, así que solo el TP puede delegarse al
exchange (se ejecuta aunque el bot esté caído); el stop-loss se sigue
vigilando localmente. Antes de una venta MARKET hay que cancelar la orden TP
para liberar el saldo que retiene.
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import MexcError
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine

BUY_FILL = {"orderId": "buy1", "status": "FILLED",
            "executedQty": "0.001", "cummulativeQuoteQty": "40.0"}


class FakeClient:
    def __init__(self, api_key="", api_secret=""):
        self.order_responses: list = []   # respuestas de new_order, en orden
        self.query_responses: dict = {}   # orderId -> respuesta de query_order
        self.orders: list[dict] = []
        self.cancels: list[str] = []
        self.fail_cancel = False

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        resp = self.order_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def query_order(self, symbol, order_id):
        resp = self.query_responses.get(order_id)
        if resp is None:
            raise MexcError("orden no encontrada")
        return resp

    def cancel_order(self, symbol, order_id):
        if self.fail_cancel:
            raise MexcError("no se pudo cancelar")
        self.cancels.append(order_id)
        return {}


def _make_engine(tmp_path, monkeypatch) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0))
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode="live",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def _buy_with_tp(eng):
    """Compra con fill inmediato cuya orden TP se coloca como 'tp1'."""
    eng.client.order_responses = [dict(BUY_FILL), {"orderId": "tp1"}]
    return eng._market_buy(40000.0)


def test_buy_places_tp_limit_order(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _buy_with_tp(eng)

    tp_order = eng.client.orders[1]
    assert tp_order["side"] == "SELL"
    assert tp_order["order_type"] == "LIMIT"
    assert tp_order["price"] == pytest.approx(pos.take_profit)
    assert tp_order["quantity"] == pytest.approx(pos.quantity)
    assert pos.tp_order_id == "tp1"
    # Persistido: al restaurar, la posición recuerda su orden TP.
    assert eng.store.load_open_positions("t")[0].tp_order_id == "tp1"


def test_tp_placement_failure_falls_back_to_local(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    eng.client.order_responses = [dict(BUY_FILL), MexcError("saldo insuficiente")]
    pos = eng._market_buy(40000.0)

    # La compra sigue registrada; el TP se vigilará localmente.
    assert pos is not None and pos.tp_order_id is None
    assert len(eng.risk.open_positions) == 1


def test_exchange_tp_fill_closes_position(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    _buy_with_tp(eng)
    # La orden TP se ejecutó (p. ej. con el bot caído) a 41600 de media.
    eng.client.query_responses["tp1"] = {
        "status": "FILLED", "executedQty": "0.001", "cummulativeQuoteQty": "41.6"}

    eng._check_exchange_tp()

    assert not eng.risk.open_positions
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "take_profit"
    assert trade["exit_price"] == pytest.approx(41600.0)
    assert trade["pnl"] == pytest.approx((41600.0 - 40000.0) * 0.001)


def test_stop_loss_cancels_tp_before_selling(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _buy_with_tp(eng)
    # Tras cancelar, la orden TP no había ejecutado nada.
    eng.client.query_responses["tp1"] = {
        "status": "CANCELED", "executedQty": "0", "cummulativeQuoteQty": "0"}
    eng.client.order_responses = [{"orderId": "sell1", "status": "FILLED",
                                   "executedQty": "0.001",
                                   "cummulativeQuoteQty": "39.0"}]

    eng._market_sell(pos, 39100.0, "stop_loss")

    assert eng.client.cancels == ["tp1"]
    assert not eng.risk.open_positions
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "stop_loss"
    assert trade["exit_price"] == pytest.approx(39000.0)


def test_sell_aborts_if_tp_cannot_be_canceled(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _buy_with_tp(eng)
    eng.client.fail_cancel = True  # y query_order tampoco sabe nada de 'tp1'

    eng._market_sell(pos, 39100.0, "stop_loss")

    # No se vendió: la posición sigue abierta y se reintentará.
    assert eng.risk.open_positions == [pos]
    assert pos.tp_order_id == "tp1"
    assert eng.store.fetch_trades() == []


def test_tp_already_filled_when_canceling(tmp_path, monkeypatch):
    # Al ir a vender por SL, resulta que la orden TP ya lo había vendido todo.
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _buy_with_tp(eng)
    eng.client.fail_cancel = True
    eng.client.query_responses["tp1"] = {
        "status": "FILLED", "executedQty": "0.001", "cummulativeQuoteQty": "41.6"}

    eng._market_sell(pos, 39100.0, "stop_loss")

    assert not eng.risk.open_positions
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "take_profit"  # el cierre real fue el TP
    assert trade["exit_price"] == pytest.approx(41600.0)
