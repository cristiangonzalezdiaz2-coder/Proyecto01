"""Pruebas del dimensionado dinámico de posiciones (sizing).

Modos: fixed (quote_per_trade, por defecto), balance_pct (un % del balance
disponible) y risk_pct (arriesgar un % fijo del balance por operación:
importe = balance * sizing_pct / stop_loss_pct). En live el balance es el
saldo real de la moneda cotizada; en paper/backtest, una equity simulada que
compone con el PnL realizado.
"""
import pandas as pd
import pytest
import yaml

from backtest import simulate
from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig, load_config
from src.mexc import MexcError, SymbolInfo
from src.risk import RiskManager
from src.strategies import Signal
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine


# ------------------------- RiskManager.position_size -------------------------
def test_fixed_ignores_balance():
    rm = RiskManager(RiskConfig(quote_per_trade=20.0))
    assert rm.position_size(10_000.0) == 20.0


def test_balance_pct():
    rm = RiskManager(RiskConfig(sizing="balance_pct", sizing_pct=0.1))
    assert rm.position_size(1000.0) == pytest.approx(100.0)


def test_risk_pct_uses_stop_distance():
    # Arriesgar el 1% con stop del 2% -> posición del 50% del balance.
    rm = RiskManager(RiskConfig(sizing="risk_pct", sizing_pct=0.01,
                                stop_loss_pct=0.02))
    assert rm.position_size(1000.0) == pytest.approx(500.0)


def test_size_capped_at_available_balance():
    rm = RiskManager(RiskConfig(sizing="risk_pct", sizing_pct=0.05,
                                stop_loss_pct=0.02))
    # 0.05/0.02 = 2.5x el balance -> se capa al balance.
    assert rm.position_size(100.0) == pytest.approx(100.0)


def test_unknown_balance_falls_back_to_fixed():
    rm = RiskManager(RiskConfig(sizing="balance_pct", sizing_pct=0.1,
                                quote_per_trade=20.0))
    assert rm.position_size(None) == 20.0


def test_build_position_with_explicit_quote():
    rm = RiskManager(RiskConfig(quote_per_trade=20.0))
    pos = rm.build_position("BTCUSDT", 100.0, quote_amount=250.0)
    assert pos.quantity == pytest.approx(2.5)


# ------------------------- Validación de config -------------------------
def test_invalid_sizing_rejected_at_load(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "symbol": "BTCUSDT", "strategy": {"name": "rsi"},
        "risk": {"sizing": "typo_pct", "sizing_pct": 0.1},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="sizing"):
        load_config(str(cfg_file))


def test_dynamic_sizing_requires_pct(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "symbol": "BTCUSDT", "strategy": {"name": "rsi"},
        "risk": {"sizing": "balance_pct"},  # sin sizing_pct
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="sizing_pct"):
        load_config(str(cfg_file))


def test_valid_sizing_loads(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "symbol": "BTCUSDT", "strategy": {"name": "rsi"},
        "risk": {"sizing": "risk_pct", "sizing_pct": 0.01, "paper_balance": 500},
    }), encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.bots[0].risk.sizing == "risk_pct"
    assert cfg.bots[0].risk.paper_balance == 500


# ------------------------- Backtest (interés compuesto) -------------------------
class BuyAt:
    name = "buy_at"

    def __init__(self, idxs):
        self.idxs = set(idxs)

    def generate_signal(self, candles):
        return Signal.BUY if len(candles) - 1 in self.idxs else Signal.HOLD


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_simulate_compounds_equity():
    # Dos operaciones ganadoras (TP +4%): la segunda se dimensiona con la
    # equity ya aumentada -> interés compuesto.
    df = _df([
        [100, 100, 100, 100],
        [100, 100, 100, 100],
        [100, 100, 100, 100],   # compra 1 @100 (10% de 1000 = 100 -> qty 1)
        [100, 105, 100, 104],   # TP 104 -> pnl +4, equity 1004
        [100, 100, 100, 100],   # compra 2 @100 (10% de 1004 = 100.4)
        [100, 105, 100, 104],   # TP 104 -> pnl +4.016
    ])
    cfg = RiskConfig(sizing="balance_pct", sizing_pct=0.1, paper_balance=1000.0)
    res = simulate(df, "BTCUSDT", BuyAt([2, 4]), fee_pct=0.0, risk_cfg=cfg)
    assert res["trades"] == 2 and res["wins"] == 2
    assert res["pnl"] == pytest.approx(4.0 + 100.4 * 0.04)
    assert res["final_equity"] == pytest.approx(1000.0 + res["pnl"])


# ------------------------- Motor -------------------------
class PaperClient:
    def __init__(self, api_key="", api_secret=""):
        pass

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        raise MexcError("sin red en tests")


def test_paper_engine_sizes_from_simulated_equity(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "MexcSpotClient", PaperClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(sizing="balance_pct", sizing_pct=0.1,
                                    paper_balance=1000.0, fee_pct=0.0))
    cfg = AppConfig(api_key="", api_secret="", trading_mode="paper",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    eng = TradingEngine(cfg, bot)

    pos = eng._market_buy(100.0)
    assert pos.quantity == pytest.approx(1.0)  # 10% de 1000 = 100 @ 100

    eng._market_sell(pos, 104.0, "take_profit")  # pnl +4 -> equity 1004
    pos2 = eng._market_buy(100.0)
    assert pos2.quantity == pytest.approx(1.004)  # 10% de 1004


INFO = SymbolInfo(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
                  base_precision=6, quote_precision=2, min_quote_amount=1.0,
                  min_quote_amount_market=1.0, min_base_size=0.0,
                  trading_allowed=True)


class LiveClient:
    def __init__(self, api_key="", api_secret=""):
        self.orders: list[dict] = []
        self.order_responses: list = []

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        return INFO

    def get_balance(self, asset):
        return {"USDT": 500.0, "BTC": 1.0}[asset]

    def new_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.order_responses.pop(0)

    def query_order(self, symbol, order_id):
        raise MexcError("orden no encontrada")

    def cancel_order(self, symbol, order_id):
        return {}


def test_live_engine_sizes_from_exchange_balance(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "MexcSpotClient", LiveClient)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(sizing="balance_pct", sizing_pct=0.2,
                                    fee_pct=0.0))
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode="live",
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    eng = TradingEngine(cfg, bot)
    eng.client.order_responses = [
        {"orderId": "1", "status": "FILLED",
         "executedQty": "0.0025", "cummulativeQuoteQty": "100.0"},
        {"orderId": "tp1"},
    ]

    eng._market_buy(40000.0)
    # 20% del balance USDT real (500) = 100 en la orden de compra.
    assert eng.client.orders[0]["quote_order_qty"] == pytest.approx(100.0)
