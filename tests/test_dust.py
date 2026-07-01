"""Pruebas del cierre administrativo de posiciones 'dust' (polvo).

Una posición cuya cantidad no alcanza el mínimo vendible (o se trunca a 0)
reintentaría y notificaría el cierre en cada ciclo para siempre. Tras
DUST_CLOSE_AFTER intentos consecutivos, el motor la retira del seguimiento
con un cierre administrativo (motivo 'dust', PnL 0).
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


class FakeClient:
    def __init__(self, api_key="", api_secret=""):
        self.order_responses: list = []
        self.orders: list[dict] = []

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        return INFO

    def get_balance(self, asset):
        return 1.0  # saldo de sobra: el problema es la precisión, no el saldo

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.order_responses.pop(0)

    def query_order(self, symbol, order_id):
        raise MexcError("orden no encontrada")

    def cancel_order(self, symbol, order_id):
        return {}


def _make_engine(tmp_path, monkeypatch) -> TradingEngine:
    monkeypatch.setattr(engine_mod, "MexcSpotClient", FakeClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0))
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode="live",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def _dust_position(eng):
    """Posición cuya cantidad se trunca a 0 con la precisión del símbolo."""
    pos = eng.risk.build_position("BTCUSDT", 40000.0)
    pos.quantity = 1e-07  # < 1e-06 (base_precision=6) -> floor = 0
    eng.risk.register_open(pos)
    eng.store.add_position(pos, bot="t")
    return pos


def test_dust_position_removed_after_repeated_failures(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _dust_position(eng)

    # Los primeros intentos fallan pero la posición sigue en seguimiento.
    for _ in range(TradingEngine.DUST_CLOSE_AFTER - 1):
        eng._market_sell(pos, 39000.0, "stop_loss")
        assert pos in eng.risk.open_positions
        assert eng.store.fetch_trades() == []

    # El intento N la retira con un cierre administrativo.
    eng._market_sell(pos, 39000.0, "stop_loss")
    assert pos not in eng.risk.open_positions
    assert not eng.store.fetch_open_positions()
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "dust"
    assert trade["pnl"] == 0.0
    assert eng.client.orders == []  # nunca llegó a enviarse una orden de venta


def test_successful_close_resets_failure_counter(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path, monkeypatch)
    pos = _dust_position(eng)

    eng._market_sell(pos, 39000.0, "stop_loss")
    eng._market_sell(pos, 39000.0, "stop_loss")  # 2 fallos acumulados

    # La cantidad pasa a ser vendible (p. ej. tras reconciliar): venta normal.
    pos.quantity = 0.001
    eng.client.order_responses = [{"orderId": "s1", "status": "FILLED",
                                   "executedQty": "0.001",
                                   "cummulativeQuoteQty": "39.0"}]
    eng._market_sell(pos, 39000.0, "stop_loss")

    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "stop_loss"  # cierre real, no 'dust'
    assert not eng.risk.open_positions
    assert eng._close_failures == {}  # contador limpio
