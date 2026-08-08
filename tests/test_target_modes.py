import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coil_core


def _state_db_rh(db_C, rh_pct, P=coil_core.P_ATM):
    # Simple deterministic stand-in so this regression test does not require CoolProp.
    W = 0.010 + (rh_pct - 50.0) * 0.00002
    cp = 1006.0 + W * 1860.0
    h = cp * db_C + W * 2_501_000.0
    return {
        "T_C": db_C,
        "RH_pct": rh_pct,
        "W": W,
        "h_J_kgda": h,
        "Vda_m3_kgda": 0.85,
        "cp_da": cp,
        "Twb_C": db_C - 5.0,
        "Tdp_C": db_C - 10.0,
        "rho_ha": (1.0 + W) / 0.85,
    }


def _state_db_wb(db_C, wb_C, P=coil_core.P_ATM):
    W = 0.009
    cp = 1006.0 + W * 1860.0
    h = cp * db_C + W * 2_501_000.0
    return {
        "T_C": db_C,
        "RH_pct": 90.0,
        "W": W,
        "h_J_kgda": h,
        "Vda_m3_kgda": 0.82,
        "cp_da": cp,
        "Twb_C": wb_C,
        "Tdp_C": wb_C - 1.0,
        "rho_ha": (1.0 + W) / 0.82,
    }


def test_target_load_db_wb_uses_supplied_air_volume(monkeypatch):
    monkeypatch.setattr(coil_core, "air_state_from_db_rh", _state_db_rh)
    monkeypatch.setattr(coil_core, "air_state_from_db_wb", _state_db_wb)

    ain = coil_core.AirCondition(db_C=27.0, rh_pct=50.0)
    result = coil_core.target_load_db_wb(ain, 13.0, 12.0, 2.50)

    assert abs(result["mdot_da_kg_s"] - 2.50 / 0.85) < 1e-12
    assert result["Q_required_kW"] > 0.0
    assert result["target_air"]["T_C"] == 13.0
    assert result["target_air"]["Twb_C"] == 12.0
