import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from coil_core import (CoilGeometry, AirCondition, HydraulicInputs, thermal_performance,
                       geometry_areas, churchill_friction_factor, water_side_htc,
                       water_pressure_drop, MM, HAS_COOLPROP)


def test_geometry_orientation():
    g = CoilGeometry(1.2, 0.85, 6, 25.4*MM, 22*MM, 9.53*MM, 0.35*MM, 10, 0.12*MM)
    a = geometry_areas(g)
    assert a["n_tubes_per_row"] == int(0.85 // (25.4*MM))
    assert a["n_fins"] == int(1.2 // (0.0254/10))
    assert a["free_flow_area_m2"] > 0


def test_churchill_positive():
    assert churchill_friction_factor(10000, 1e-4) > 0
    assert churchill_friction_factor(1000, 1e-4) == pytest.approx(0.064)


def test_hydraulic_paths_without_coolprop():
    g = CoilGeometry(1.2, 0.85, 6, 25.4*MM, 22*MM, 9.53*MM, 0.35*MM, 10, 0.12*MM)
    geom = geometry_areas(g)
    hyd = HydraulicInputs(12, 1.5, 42.4*MM, 1.5*MM, 42.4*MM, 1.5*MM, 0.85)
    props = {"rho": 999.0, "mu": 1.3e-3, "cp": 4200.0, "k": 0.58}
    wh = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props, hyd.tube_roughness_m)
    out = water_pressure_drop(geom, hyd, props, wh)
    assert len(out["table"]) == 12
    assert out["dp_total_avg_kPa"] > 0
    assert out["header_supply_ID_mm"] == pytest.approx(39.4, abs=0.01)
    assert out["dp_total_max_kPa"] >= out["dp_total_min_kPa"]


@pytest.mark.skipif(not HAS_COOLPROP, reason="CoolProp not installed in test runtime")
def test_full_case():
    g = CoilGeometry(1.2, 0.85, 6, 25.4*MM, 22*MM, 9.53*MM, 0.35*MM, 10, 0.12*MM)
    a = AirCondition(27, 50)
    h = HydraulicInputs(12, 1.5, 42.4*MM, 1.5*MM, 42.4*MM, 1.5*MM, 0.85)
    r = thermal_performance(g, a, 2.04, "Water", 0, 7, 300000, h)
    assert r["Q_total_kW"] > 0
    assert r["water_out_C"] > 7
    assert r["air_out"]["T_C"] < 27
    assert r["air_dp_Pa"] > 0
