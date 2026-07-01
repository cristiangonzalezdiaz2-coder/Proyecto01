"""Pruebas de la persistencia en SQLite (usa una BD temporal)."""
from src.persistence import PositionStore
from src.risk import Position


def _store(tmp_path):
    return PositionStore(str(tmp_path / "test.db"))


def test_add_and_load_open_positions(tmp_path):
    store = _store(tmp_path)
    pos = Position("BTCUSDT", 100.0, 0.5, 90.0, 120.0)
    pid = store.add_position(pos)
    assert pid == pos.id

    # Una nueva instancia (simula reinicio) debe recuperar la posición.
    store2 = PositionStore(str(tmp_path / "test.db"))
    loaded = store2.load_open_positions()
    assert len(loaded) == 1
    assert loaded[0].symbol == "BTCUSDT"
    assert loaded[0].entry_price == 100.0
    assert loaded[0].id == pid


def test_close_position_removes_from_open(tmp_path):
    store = _store(tmp_path)
    pos = Position("ETHUSDT", 50.0, 1.0, 45.0, 60.0)
    store.add_position(pos)
    store.close_position(pos, exit_price=60.0, pnl=10.0, reason="take_profit")

    # Ya no debe aparecer entre las abiertas.
    assert store.load_open_positions() == []


def test_daily_state_roundtrip(tmp_path):
    store = _store(tmp_path)
    assert store.load_daily_state("2026-07-01") is None

    store.save_daily_state("2026-07-01", -12.5, True)
    pnl, halted = store.load_daily_state("2026-07-01")
    assert pnl == -12.5
    assert halted is True

    # Upsert: sobrescribe el mismo día.
    store.save_daily_state("2026-07-01", 3.0, False)
    pnl, halted = store.load_daily_state("2026-07-01")
    assert pnl == 3.0
    assert halted is False
