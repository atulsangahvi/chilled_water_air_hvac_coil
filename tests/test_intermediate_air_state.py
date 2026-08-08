import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coil_core


def test_air_state_from_T_W_contains_volume_and_density(monkeypatch):
    # Stand-in for CoolProp so the regression test runs in a lightweight CI environment.
    monkeypatch.setattr(coil_core, "_need_coolprop", lambda: None)

    def fake_ha(output, *args):
        vals = {
            "H": 45000.0,
            "R": 0.75,
            "D": 285.15,
            "B": 289.15,
            "Vda": 0.86,
        }
        return vals[output]

    monkeypatch.setattr(coil_core, "HAPropsSI", fake_ha)
    s = coil_core.air_state_from_T_W(14.0, 0.009, 101325.0)

    assert s["Vda_m3_kgda"] == 0.86
    assert s["rho_ha"] > 0.0
    assert s["cp_da"] > 1000.0
    assert s["T_C"] == 14.0


def test_air_state_from_T_W_has_ideal_gas_fallback(monkeypatch):
    monkeypatch.setattr(coil_core, "_need_coolprop", lambda: None)

    def fake_ha(output, *args):
        if output == "Vda":
            raise ValueError("simulate unavailable Vda property")
        vals = {
            "H": 45000.0,
            "R": 0.75,
            "D": 285.15,
            "B": 289.15,
        }
        return vals[output]

    monkeypatch.setattr(coil_core, "HAPropsSI", fake_ha)
    s = coil_core.air_state_from_T_W(14.0, 0.009, 101325.0)

    assert s["Vda_m3_kgda"] > 0.0
    assert s["rho_ha"] > 0.0
