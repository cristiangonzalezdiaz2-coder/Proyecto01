"""Pruebas del gestor de riesgo global compartido entre bots."""
import threading

import yaml

from src.config import GlobalRiskConfig, load_config
from src.risk import GlobalRiskManager, Position


def _pos(entry, qty, pid=None):
    return Position("BTCUSDT", entry, qty, entry * 0.98, entry * 1.04, id=pid)


def test_disabled_allows_everything():
    gr = GlobalRiskManager(GlobalRiskConfig())  # enabled=False
    ok, err = gr.can_open(1_000_000)
    assert ok and err is None


def test_max_exposure_blocks_when_exceeded():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_total_exposure=100))
    # Exposición actual 60 (precio 60 * qty 1).
    gr.register_open(_pos(60, 1, pid=1))
    ok, _ = gr.can_open(30)   # 60 + 30 = 90 <= 100
    assert ok
    ok, err = gr.can_open(50)  # 60 + 50 = 110 > 100
    assert not ok and "exposición" in err


def test_max_open_positions_global():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_open_positions=2))
    gr.register_open(_pos(10, 1, pid=1))
    gr.register_open(_pos(10, 1, pid=2))
    ok, err = gr.can_open(10)
    assert not ok and "posiciones" in err


def test_daily_loss_halts_all():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_daily_loss=20))
    p = _pos(100, 1, pid=1)
    gr.register_open(p)
    gr.register_close(p, -25.0)  # pérdida supera el máximo diario global
    assert gr.halted
    ok, err = gr.can_open(1)
    assert not ok and "diaria" in err


def test_close_frees_exposure():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_total_exposure=100))
    p = _pos(80, 1, pid=1)
    gr.register_open(p)
    assert gr.snapshot()["exposure"] == 80
    gr.register_close(p, 5.0)
    assert gr.snapshot()["exposure"] == 0
    assert gr.snapshot()["open_count"] == 0


def test_initialize_from_open_positions():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_total_exposure=200))
    gr.initialize([_pos(50, 1, pid=1), _pos(30, 2, pid=2)], daily_pnl=-5.0)
    snap = gr.snapshot()
    assert snap["exposure"] == 50 + 60  # 50*1 + 30*2
    assert snap["open_count"] == 2
    assert snap["daily_pnl"] == -5.0


def test_reset_daily_idempotent_per_day():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_daily_loss=20))
    p = _pos(100, 1, pid=1)
    gr.register_open(p)
    gr.register_close(p, -25.0)
    assert gr.halted
    gr.reset_daily("2026-07-02")
    assert not gr.halted and gr.snapshot()["daily_pnl"] == 0.0
    # Segunda llamada con el mismo día no vuelve a resetear si acumuló algo.
    p2 = _pos(100, 1, pid=2)
    gr.register_open(p2); gr.register_close(p2, -3.0)
    gr.reset_daily("2026-07-02")  # mismo día -> no resetea
    assert gr.snapshot()["daily_pnl"] == -3.0


# ------------------- Reserva atómica (reserve/confirm/release) -------------------
def test_reserve_is_atomic_check_and_hold():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_total_exposure=50))
    ok, _ = gr.reserve(30)
    assert ok and gr.snapshot()["exposure"] == 30
    ok, err = gr.reserve(30)  # 30 + 30 > 50: la reserva anterior ya cuenta
    assert not ok and "exposición" in err
    gr.release(30)
    assert gr.snapshot()["exposure"] == 0
    assert gr.reserve(30)[0]


def test_confirm_adjusts_reservation_to_real_fill():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_total_exposure=100))
    gr.reserve(30)
    p = _pos(28, 1, pid=7)  # el fill real comprometió 28, no los 30 reservados
    gr.confirm(p, 30)
    snap = gr.snapshot()
    assert snap["exposure"] == 28 and snap["open_count"] == 1
    gr.register_close(p, 1.0)
    snap = gr.snapshot()
    assert snap["exposure"] == 0 and snap["open_count"] == 0


def test_reserve_respects_max_open_positions():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_open_positions=1))
    assert gr.reserve(10)[0]
    ok, err = gr.reserve(10)
    assert not ok and "posiciones" in err


def test_concurrent_reserves_never_exceed_limit():
    gr = GlobalRiskManager(GlobalRiskConfig(enabled=True, max_open_positions=5))
    results = []

    def worker():
        results.append(gr.reserve(1)[0])

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 5  # exactamente 5 reservas aceptadas, ni una más
    assert gr.snapshot()["open_count"] == 5


def test_config_parsing(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({
        "symbol": "BTCUSDT", "strategy": {"name": "rsi"},
        "global_risk": {"max_total_exposure": 100, "max_daily_loss": 50, "max_open_positions": 3},
    }), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.global_risk.enabled is True
    assert cfg.global_risk.max_total_exposure == 100.0
    assert cfg.global_risk.max_open_positions == 3
