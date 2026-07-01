"""Pruebas de las comisiones (fee_pct) en el PnL.

El PnL de cada operación es NETO: descuenta la comisión de la compra
(sobre el importe de entrada) y la de la venta (sobre el de salida).
Aplica por igual a live, paper, backtest y walk-forward, porque todos
cierran posiciones a través de RiskManager.register_close.
"""
import pytest
import yaml

from src.config import RiskConfig, load_config
from src.risk import RiskManager


def _manager(**kwargs) -> RiskManager:
    return RiskManager(RiskConfig(**kwargs))


def test_pnl_is_net_of_both_fees():
    rm = _manager(fee_pct=0.001)
    pos = rm.build_position("BTCUSDT", 100.0)
    pos.quantity = 1.0
    pnl = rm.register_close(pos, 110.0)
    # Bruto 10; comisiones = (100 + 110) * 1 * 0.001 = 0.21
    assert pnl == pytest.approx(10.0 - 0.21)
    assert rm.daily_pnl == pytest.approx(10.0 - 0.21)


def test_zero_fee_keeps_gross_pnl():
    rm = _manager(fee_pct=0.0)
    pos = rm.build_position("BTCUSDT", 100.0)
    pos.quantity = 1.0
    assert rm.register_close(pos, 110.0) == pytest.approx(10.0)


def test_fees_can_turn_flat_trade_negative():
    rm = _manager(fee_pct=0.0005)
    pos = rm.build_position("BTCUSDT", 100.0)
    pos.quantity = 1.0
    # Salir al mismo precio pierde exactamente las comisiones.
    assert rm.register_close(pos, 100.0) == pytest.approx(-0.1)


def test_trade_fees_helper():
    rm = _manager(fee_pct=0.0005)
    pos = rm.build_position("BTCUSDT", 40000.0)  # qty = 20/40000 = 0.0005
    fees = rm.trade_fees(pos, 42000.0)
    assert fees == pytest.approx((40000.0 + 42000.0) * 0.0005 * 0.0005)


def test_default_fee_is_mexc_taker():
    assert RiskConfig().fee_pct == pytest.approx(0.0005)


def test_fee_pct_loaded_and_inherited(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "risk": {"fee_pct": 0.001},
        "bots": [
            {"name": "a", "symbol": "BTCUSDT", "strategy": {"name": "rsi"}},
            {"name": "b", "symbol": "ETHUSDT", "strategy": {"name": "rsi"},
             "risk": {"fee_pct": 0.0}},
        ],
    }), encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.bots[0].risk.fee_pct == 0.001  # heredado de la raíz
    assert cfg.bots[1].risk.fee_pct == 0.0    # propio del bot
