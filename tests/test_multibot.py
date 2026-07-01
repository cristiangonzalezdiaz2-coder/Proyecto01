"""Pruebas de la configuración multi-bot y la separación por bot en la BD."""
import yaml

from src.config import load_config
from src.persistence import PositionStore
from src.risk import Position


def _write_cfg(tmp_path, data: dict) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


# --------------------------- Config ---------------------------
def test_single_bot_backcompat(tmp_path):
    cfg_path = _write_cfg(tmp_path, {
        "symbol": "BTCUSDT", "interval": "1h",
        "strategy": {"name": "rsi", "period": 10},
        "risk": {"quote_per_trade": 30.0},
    })
    cfg = load_config(cfg_path)
    assert len(cfg.bots) == 1
    assert cfg.symbol == "BTCUSDT"          # propiedad de compatibilidad
    assert cfg.bots[0].strategy.name == "rsi"
    assert cfg.bots[0].strategy.params == {"period": 10}
    assert cfg.risk.quote_per_trade == 30.0


def test_multi_bot_inherits_root(tmp_path):
    cfg_path = _write_cfg(tmp_path, {
        "interval": "30m",
        "risk": {"quote_per_trade": 20.0, "stop_loss_pct": 0.02},
        "bots": [
            {"name": "a", "symbol": "BTCUSDT",
             "strategy": {"name": "ma_crossover", "fast_period": 5, "slow_period": 20}},
            {"name": "b", "symbol": "ETHUSDT", "interval": "15m",
             "strategy": {"name": "rsi"}, "risk": {"quote_per_trade": 10.0}},
        ],
    })
    cfg = load_config(cfg_path)
    assert len(cfg.bots) == 2
    a, b = cfg.bots
    assert a.name == "a" and a.symbol == "BTCUSDT"
    assert a.interval == "30m"              # heredado de la raíz
    assert a.risk.quote_per_trade == 20.0   # heredado
    assert b.interval == "15m"              # propio
    assert b.risk.quote_per_trade == 10.0   # propio
    assert b.risk.stop_loss_pct == 0.02     # heredado de la raíz


def test_duplicate_bot_names_made_unique(tmp_path):
    cfg_path = _write_cfg(tmp_path, {
        "bots": [
            {"name": "x", "symbol": "BTCUSDT", "strategy": {"name": "rsi"}},
            {"name": "x", "symbol": "ETHUSDT", "strategy": {"name": "macd"}},
        ],
    })
    cfg = load_config(cfg_path)
    names = [b.name for b in cfg.bots]
    assert len(set(names)) == 2  # el segundo 'x' se renombra


# --------------------------- Persistencia por bot ---------------------------
def test_positions_isolated_per_bot(tmp_path):
    store = PositionStore(str(tmp_path / "bots.db"))
    store.add_position(Position("BTCUSDT", 100, 1, 90, 110), bot="a")
    store.add_position(Position("ETHUSDT", 50, 2, 45, 60), bot="b")

    assert len(store.load_open_positions("a")) == 1
    assert len(store.load_open_positions("b")) == 1
    assert len(store.load_open_positions()) == 2  # todas
    assert store.load_open_positions("a")[0].symbol == "BTCUSDT"


def test_daily_state_isolated_per_bot(tmp_path):
    store = PositionStore(str(tmp_path / "bots.db"))
    store.save_daily_state("2026-07-01", -5.0, True, bot="a")
    store.save_daily_state("2026-07-01", 3.0, False, bot="b")

    assert store.load_daily_state("2026-07-01", bot="a") == (-5.0, True)
    assert store.load_daily_state("2026-07-01", bot="b") == (3.0, False)


def test_fetch_bots_lists_distinct(tmp_path):
    store = PositionStore(str(tmp_path / "bots.db"))
    p = Position("BTCUSDT", 100, 1, 90, 110)
    store.add_position(p, bot="a")
    store.close_position(p, 110, 10.0, "take_profit", bot="a")
    store.add_position(Position("ETHUSDT", 50, 2, 45, 60), bot="b")
    assert sorted(store.fetch_bots()) == ["a", "b"]
