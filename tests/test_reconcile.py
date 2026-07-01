"""Pruebas de la reconciliación de balances al arrancar en modo live.

Al reiniciar, las posiciones restauradas de la BD deben estar respaldadas por
el balance real del exchange (libre + bloqueado en órdenes TP). Si falta saldo
(venta manual, otra app, etc.), la posición se reduce o se descarta, avisando.
"""
import pytest

from src.config import AppConfig, BotConfig, RiskConfig, StrategyConfig
from src.mexc import SymbolInfo
from src.persistence import PositionStore
from src.risk import Position
from src.trading import engine as engine_mod
from src.trading.engine import TradingEngine

INFO = SymbolInfo(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
                  base_precision=6, quote_precision=2, min_quote_amount=1.0,
                  min_quote_amount_market=1.0, min_base_size=0.0,
                  trading_allowed=True)


class FakeClient:
    balances = (0.0, 0.0)   # (free, locked); se fija por subclase en cada test
    balance_calls = 0

    def __init__(self, api_key="", api_secret=""):
        pass

    def ping(self):
        return {}

    def get_symbol_info(self, symbol):
        return INFO

    def get_asset_balance(self, asset):
        type(self).balance_calls += 1
        return type(self).balances


def _seed_position(db_path: str, qty: float) -> None:
    store = PositionStore(db_path)
    store.add_position(
        Position("BTCUSDT", 40000.0, qty, 39200.0, 41600.0), bot="t")
    store.close()


def _make_engine(tmp_path, monkeypatch, free, locked=0.0, mode="live") -> TradingEngine:
    client_cls = type("Client", (FakeClient,), {"balances": (free, locked),
                                                "balance_calls": 0})
    monkeypatch.setattr(engine_mod, "MexcSpotClient", client_cls)
    bot = BotConfig(name="t", symbol="BTCUSDT", interval="1h", poll_seconds=60,
                    strategy=StrategyConfig(),
                    risk=RiskConfig(quote_per_trade=20.0, fee_pct=0.0))
    cfg = AppConfig(api_key="k", api_secret="s", trading_mode=mode,
                    bots=[bot], db_path=str(tmp_path / "t.db"))
    return TradingEngine(cfg, bot)


def test_balance_covers_positions(tmp_path, monkeypatch):
    _seed_position(str(tmp_path / "t.db"), qty=0.001)
    eng = _make_engine(tmp_path, monkeypatch, free=0.002)
    assert len(eng.risk.open_positions) == 1
    assert eng.risk.open_positions[0].quantity == pytest.approx(0.001)


def test_locked_balance_counts_as_backing(tmp_path, monkeypatch):
    # El saldo retenido por una orden TP nuestra también respalda la posición.
    _seed_position(str(tmp_path / "t.db"), qty=0.001)
    eng = _make_engine(tmp_path, monkeypatch, free=0.0, locked=0.001)
    assert len(eng.risk.open_positions) == 1
    assert eng.risk.open_positions[0].quantity == pytest.approx(0.001)


def test_insufficient_balance_shrinks_position(tmp_path, monkeypatch):
    _seed_position(str(tmp_path / "t.db"), qty=0.001)
    eng = _make_engine(tmp_path, monkeypatch, free=0.0006)

    assert eng.risk.open_positions[0].quantity == pytest.approx(0.0006)
    # Persistido: la BD refleja la cantidad reconciliada.
    assert eng.store.load_open_positions("t")[0].quantity == pytest.approx(0.0006)


def test_no_balance_drops_position(tmp_path, monkeypatch):
    _seed_position(str(tmp_path / "t.db"), qty=0.001)
    eng = _make_engine(tmp_path, monkeypatch, free=0.0)

    assert not eng.risk.open_positions
    assert not eng.store.fetch_open_positions()
    trade = eng.store.fetch_trades()[0]
    assert trade["reason"] == "reconcile"
    assert trade["pnl"] == 0.0


def test_paper_mode_skips_reconciliation(tmp_path, monkeypatch):
    _seed_position(str(tmp_path / "t.db"), qty=0.001)
    eng = _make_engine(tmp_path, monkeypatch, free=0.0, mode="paper")

    assert len(eng.risk.open_positions) == 1  # intacta aunque no haya balance
    assert type(eng.client).balance_calls == 0
