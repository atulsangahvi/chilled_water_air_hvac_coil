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


def test_circuit_compatibility_and_auto_route():
    from circuiting import compatibility_summary, auto_serpentine_routes, validate_routes
    # 6 rows x 8 tubes = 48 total, 6 circuits -> 8 passes/circuit, valid same-end circuiting.
    comp = compatibility_summary(48, 6, "Same tube end (even passes/circuit required)")
    assert comp["fully_compatible"]
    assert comp["passes_per_circuit"] == 8
    routes = auto_serpentine_routes(6, 8, 6, "Same tube end (even passes/circuit required)")
    val = validate_routes(routes, 6, 8, 6, "Same tube end (even passes/circuit required)", 0.0254, 0.022)
    assert val["valid"]
    assert val["complete"]
    assert val["balanced"]
    assert len({tube for route in routes.values() for tube in route}) == 48


def test_circuit_parity_rule():
    from circuiting import compatibility_summary
    # 30 tubes / 6 circuits = 5 equal passes: opposite-end is balanced.  Same-end cannot use
    # equal 5-pass circuits, but can still use an intentional unequal 4/6-pass pattern.
    op = compatibility_summary(30, 6, "Opposite tube ends (odd passes/circuit required)")
    se = compatibility_summary(30, 6, "Same tube end (even passes/circuit required)")
    assert op["fully_balanced_compatible"]
    assert se["fully_compatible"]
    assert not se["fully_balanced_compatible"]
    assert set(se["recommended_pass_counts"]) == {4, 6}


def test_explicit_circuit_hydraulic_solver_balanced_routes():
    from circuiting import auto_serpentine_routes, explicit_circuit_hydraulics
    from coil_core import HydraulicInputs
    routes = auto_serpentine_routes(4, 6, 4, "Same tube end (even passes/circuit required)")
    geom = {"tube_length_m": 1.2, "Di_m": 0.00883}
    hyd = HydraulicInputs(
        circuits=4, water_mass_flow_kg_s=1.2,
        inlet_header_od_m=0.0424, inlet_header_thickness_m=0.0015,
        outlet_header_od_m=0.0424, outlet_header_thickness_m=0.0015,
        header_length_m=0.85,
    )
    props = {"rho": 998.0, "mu": 0.0012, "cp": 4180.0, "k": 0.58}
    out = explicit_circuit_hydraulics(routes, geom, hyd, props, 6, "Top")
    assert abs(sum(out["flows_kg_s"]) - 1.2) < 1e-8
    assert len(out["table"]) == 4
    assert out["dp_total_avg_kPa"] > 0
    assert out["header_supply_velocity_m_s"] > 0


def test_tube_temperature_postprocess_conserves_row_duty():
    import pandas as pd
    from circuiting import auto_serpentine_routes, tube_temperature_postprocess
    routes = auto_serpentine_routes(4, 6, 4, "Same tube end (even passes/circuit required)")
    rows = pd.DataFrame({"Row_air_sequence":[1,2,3,4], "Q_total_kW":[5.0,6.0,7.0,8.0]})
    flows=[0.25,0.25,0.25,0.25]
    out=tube_temperature_postprocess(routes, rows, 6, flows, 7.0, 4180.0)
    assert abs(out["circuit_outlet_table"]["Circuit_Q_kW"].sum() - 26.0) < 1e-10
    expected=7.0+26000.0/(sum(flows)*4180.0)
    assert abs(out["mixed_outlet_C"]-expected) < 1e-10


def test_unequal_parity_compatible_circuiting_uses_all_tubes():
    from circuiting import compatibility_summary, auto_serpentine_routes, validate_routes
    # Typical awkward geometry: 6 rows x 33 tubes = 198 tubes, 12 same-end feeds.
    # Equal length would be 16.5 passes, impossible; a valid same-end pattern uses 16/18 passes.
    comp = compatibility_summary(198, 12, "Same tube end (even passes/circuit required)")
    assert comp["fully_compatible"]
    assert not comp["fully_balanced_compatible"]
    assert min(comp["recommended_pass_counts"]) == 16
    assert max(comp["recommended_pass_counts"]) == 18
    assert sum(comp["recommended_pass_counts"]) == 198
    routes = auto_serpentine_routes(6, 33, 12, "Same tube end (even passes/circuit required)")
    val = validate_routes(routes, 6, 33, 12, "Same tube end (even passes/circuit required)", 0.0254, 0.022)
    assert val["valid"] and val["complete"]
    assert not val["balanced"]
    assert all(len(route) % 2 == 0 for route in routes.values())


def test_explicit_hydraulics_accepts_unequal_routes():
    from circuiting import auto_serpentine_routes, explicit_circuit_hydraulics
    from coil_core import HydraulicInputs
    routes = auto_serpentine_routes(6, 33, 12, "Same tube end (even passes/circuit required)")
    geom = {"tube_length_m": 1.2, "Di_m": 0.00883}
    hyd = HydraulicInputs(
        circuits=12, water_mass_flow_kg_s=1.55,
        inlet_header_od_m=0.0424, inlet_header_thickness_m=0.0015,
        outlet_header_od_m=0.0424, outlet_header_thickness_m=0.0015,
        header_length_m=0.85,
    )
    props = {"rho": 998.0, "mu": 0.0012, "cp": 4180.0, "k": 0.58}
    out = explicit_circuit_hydraulics(routes, geom, hyd, props, 33, "Top")
    assert len(out["table"]) == 12
    assert set(out["table"]["Passes"]) == {16, 18}
    assert abs(sum(out["flows_kg_s"]) - 1.55) < 1e-8
    # Longer circuits should be allowed to attract a different flow; exact sign can depend on header position.
    assert out["flow_imbalance_pct_max"] >= 0.0
