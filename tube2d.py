from __future__ import annotations

"""Fully coupled tube-by-tube / air-lane thermal solver.

The physical coil is discretized into a Cartesian grid:
    R# = row in the airflow direction
    T# = vertical tube / air-lane position

Each grid cell contains one straight tube pass plus its associated fin strip.  Air is marched
from R1 to RN independently in each vertical lane.  Coolant is marched through the explicit
user-defined circuit route.  Since these two marches cross each other, the local tube inlet
water temperatures are iterated until the entire grid converges.

This is materially more detailed than an equivalent row-bank model.  It still assumes a
uniform entering-air mass-flow distribution among the vertical lanes and neglects lateral
cross-fin conduction between adjacent tube cells; both can be added as later refinements.
"""

from dataclasses import replace
from typing import Dict, List, Tuple
import math

import numpy as np
import pandas as pd

from coil_core import (
    AirCondition,
    CoilGeometry,
    HydraulicInputs,
    air_state_from_db_rh,
    air_state_from_T_W,
    T_from_h_W,
    coolant_props,
    crossflow_effectiveness,
    geometry_areas,
    segmented_thermal_performance,
    saturation_enthalpy,
    thermal_performance,
)
from circuiting import (
    explicit_circuit_hydraulics,
    parse_tube_id,
    tube_id,
    tube_temperature_postprocess,
)


def _mix_air_states(states: List[Dict[str, float]], weights: List[float], pressure_Pa: float) -> Dict[str, float]:
    if not states:
        raise ValueError("No air states supplied for mixing.")
    w = np.asarray(weights, dtype=float)
    if np.sum(w) <= 0:
        w = np.ones(len(states), dtype=float)
    h = float(np.average([s["h_J_kgda"] for s in states], weights=w))
    W = float(np.average([s["W"] for s in states], weights=w))
    T = float(T_from_h_W(h, W, pressure_Pa))
    base = air_state_from_T_W(T, W, pressure_Pa)
    # Enrich with Vda/rho for consistent downstream reporting.
    return air_state_from_db_rh(T, base["RH_pct"], pressure_Pa)


def _route_maps(routes: Dict[int, List[str]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    owner: Dict[str, int] = {}
    seq: Dict[str, int] = {}
    for c, route in sorted(routes.items()):
        for i, label in enumerate(route, 1):
            owner[label] = int(c)
            seq[label] = i
    return owner, seq


def _cell_geometry(g: CoilGeometry, tubes_per_row: int) -> CoilGeometry:
    # Allocate the full finned face height among the tube lanes.  The geometry routine clamps
    # the number of tubes in this strip to one, so summing all strips recovers the full row.
    lane_h = float(g.face_height_m) / max(int(tubes_per_row), 1)
    return replace(g, face_height_m=lane_h, rows=1)


def _run_grid_iteration(
    g: CoilGeometry,
    air_in_cond: AirCondition,
    total_air_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    routes: Dict[int, List[str]],
    circuit_flows_kg_s: List[float],
    tw_in_guess: Dict[str, float],
    air_htc_multiplier: float,
    air_dp_multiplier: float,
    wet_air_dp_factor: float,
    air_fouling_m2K_W: float,
    water_fouling_m2K_W: float,
) -> Dict[str, object]:
    rows = int(g.rows)
    geom_full = geometry_areas(g)
    tpr = int(geom_full["n_tubes_per_row"])
    if len(circuit_flows_kg_s) != len(routes):
        raise ValueError("Circuit-flow count does not match the number of routed circuits.")

    owner, seqmap = _route_maps(routes)
    expected = {tube_id(r, t) for t in range(1, tpr + 1) for r in range(1, rows + 1)}
    if set(owner) != expected:
        missing = len(expected - set(owner))
        extra = len(set(owner) - expected)
        raise ValueError(f"2-D solve requires a complete physical circuit map (missing={missing}, extra={extra}).")

    flow_by_c = {int(c): float(circuit_flows_kg_s[i]) for i, c in enumerate(sorted(routes))}
    cell_g = _cell_geometry(g, tpr)
    ain0 = air_state_from_db_rh(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
    mdot_da_total = total_air_m3_s / max(ain0["Vda_m3_kgda"], 1e-12)
    mdot_da_lane = mdot_da_total / max(tpr, 1)

    # Each vertical lane conserves its entering dry-air mass flow.  Volume flow therefore
    # changes from row to row as the air density changes.
    lane_air = {t: dict(ain0) for t in range(1, tpr + 1)}
    cell_results: Dict[str, Dict[str, object]] = {}
    rows_out: List[Dict[str, object]] = []

    for r in range(1, rows + 1):
        row_cells = []
        row_air_in = []
        row_air_out = []
        for t in range(1, tpr + 1):
            label = tube_id(r, t)
            c = owner[label]
            q_c = max(flow_by_c[c], 1e-12)
            a_in = lane_air[t]
            local_vdot = mdot_da_lane * a_in["Vda_m3_kgda"]
            local_hyd = replace(hyd, circuits=1, water_mass_flow_kg_s=q_c)
            rr = thermal_performance(
                cell_g,
                AirCondition(a_in["T_C"], a_in["RH_pct"], air_in_cond.pressure_Pa),
                local_vdot,
                coolant_kind,
                glycol_pct,
                float(tw_in_guess[label]),
                water_pressure_Pa,
                local_hyd,
                air_htc_multiplier,
                air_dp_multiplier,
                wet_air_dp_factor,
                air_fouling_m2K_W,
                water_fouling_m2K_W,
                air_bank_rows=rows,
                compute_hydraulics=False,
            )
            rr["Tube"] = label
            rr["water_in_C"] = float(tw_in_guess[label])
            rr["Row"] = r
            rr["Tube_position"] = t
            rr["Circuit"] = c
            rr["Sequence"] = seqmap[label]
            rr["Circuit_mass_flow_kg_s"] = q_c
            cell_results[label] = rr
            lane_air[t] = rr["air_out"]
            row_cells.append(rr)
            row_air_in.append(rr["air_in"])
            row_air_out.append(rr["air_out"])

        mix_in = _mix_air_states(row_air_in, [mdot_da_lane] * tpr, air_in_cond.pressure_Pa)
        mix_out = _mix_air_states(row_air_out, [mdot_da_lane] * tpr, air_in_cond.pressure_Pa)
        qrow = sum(float(x["Q_total_kW"]) for x in row_cells)
        qsrow = sum(float(x["Q_sensible_kW"]) for x in row_cells)
        qlrow = sum(float(x["Q_latent_kW"]) for x in row_cells)
        # Coolant does not form one serial row stream in an arbitrary circuit map, therefore
        # report local flow-weighted temperatures rather than implying a single row inlet.
        wf = np.array([x["Circuit_mass_flow_kg_s"] for x in row_cells], dtype=float)
        twi = float(np.average([x["water_in_C"] if "water_in_C" in x else tw_in_guess[x["Tube"]] for x in row_cells], weights=wf))
        two = float(np.average([x["water_out_C"] for x in row_cells], weights=wf))
        row_dp = float(np.mean([x["air_dp_Pa"] for x in row_cells]))
        wet = float(np.mean([x["wet_fraction"] for x in row_cells]))
        modes = {x["surface_mode"] for x in row_cells}
        mode = next(iter(modes)) if len(modes) == 1 else "Mixed across tubes"
        row_UA = sum(float(x["UA_dry_W_K"]) for x in row_cells)
        cp_w_local = np.average([x["water_props"]["cp"] for x in row_cells], weights=wf)
        C_air = mdot_da_total * mix_in["cp_da"]
        # The same circuit can pass through a row more than once; this is a local interacting
        # capacity-rate sum, not an independent total system water capacity rate.
        Cw_local = float(sum(x["Circuit_mass_flow_kg_s"] * x["water_props"]["cp"] for x in row_cells))
        Cmin = min(C_air, Cw_local)
        Cmax = max(C_air, Cw_local)
        Cr = Cmin / max(Cmax, 1e-12)
        NTU = row_UA / max(Cmin, 1e-12)
        eps = crossflow_effectiveness(NTU, Cr, Cmin_is_water=(Cw_local <= C_air))
        rows_out.append({
            "Row_air_sequence": r,
            "Air_in_DB_C": mix_in["T_C"],
            "Air_out_DB_C": mix_out["T_C"],
            "Air_in_WB_C": mix_in["Twb_C"],
            "Air_out_WB_C": mix_out["Twb_C"],
            "Air_in_RH_pct": mix_in["RH_pct"],
            "Air_out_RH_pct": mix_out["RH_pct"],
            "Air_in_W_g_kgda": 1000.0 * mix_in["W"],
            "Air_out_W_g_kgda": 1000.0 * mix_out["W"],
            "Water_in_C": twi,
            "Water_out_C": two,
            "Q_total_kW": qrow,
            "Q_sensible_kW": qsrow,
            "Q_latent_kW": qlrow,
            "Wet_fraction_pct": 100.0 * wet,
            "Surface_mode": mode,
            "C_air_kW_K": C_air / 1000.0,
            "C_water_kW_K": Cw_local / 1000.0,
            "Cr": Cr,
            "NTU_dry": NTU,
            "Effectiveness_dry_crossflow": eps,
            "Re_air": float(np.mean([x["air_corr"]["Re_air"] for x in row_cells])),
            "Pr_air": float(np.mean([x["air_corr"]["Pr_air"] for x in row_cells])),
            "Re_water": float(np.average([x["water_ht"]["Re_water"] for x in row_cells], weights=wf)),
            "Pr_water": float(np.average([x["water_ht"]["Pr_water"] for x in row_cells], weights=wf)),
            "Air_dP_Pa": row_dp,
        })

    # One fixed-point update along every physical coolant route.
    new_tw_in: Dict[str, float] = {}
    for c, route in sorted(routes.items()):
        for i, label in enumerate(route):
            new_tw_in[label] = float(tw_in_guess[label]) if i == 0 else float(cell_results[route[i - 1]]["water_out_C"])
    return {
        "cell_results": cell_results,
        "row_table": pd.DataFrame(rows_out),
        "new_tw_in": new_tw_in,
        "lane_air_out": lane_air,
        "mdot_da_total": mdot_da_total,
        "mdot_da_lane": mdot_da_lane,
    }


def _assemble_tables(
    routes: Dict[int, List[str]], cell_results: Dict[str, Dict[str, object]], circuit_flows_kg_s: List[float]
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    circ = []
    flow_by_c = {int(c): float(circuit_flows_kg_s[i]) for i, c in enumerate(sorted(routes))}
    for c, route in sorted(routes.items()):
        qsum = 0.0
        temps = []
        for seq, label in enumerate(route, 1):
            rr = cell_results[label]
            qsum += float(rr["Q_total_kW"])
            temps.append(0.5 * (float(rr["water_in_C"]) + float(rr["water_out_C"])))
            rows.append({
                "Circuit": int(c),
                "Sequence": seq,
                "Tube": label,
                "Row": int(rr["Row"]),
                "Tube_position": int(rr["Tube_position"]),
                "Circuit_mass_flow_kg_s": flow_by_c[int(c)],
                "Water_in_C": float(rr["water_in_C"]),
                "Water_out_C": float(rr["water_out_C"]),
                "Air_in_DB_C": float(rr["air_in"]["T_C"]),
                "Air_out_DB_C": float(rr["air_out"]["T_C"]),
                "Air_in_WB_C": float(rr["air_in"]["Twb_C"]),
                "Air_out_WB_C": float(rr["air_out"]["Twb_C"]),
                "Air_out_RH_pct": float(rr["air_out"]["RH_pct"]),
                "Q_total_kW": float(rr["Q_total_kW"]),
                "Q_sensible_kW": float(rr["Q_sensible_kW"]),
                "Q_latent_kW": float(rr["Q_latent_kW"]),
                "Wet_fraction_pct": 100.0 * float(rr["wet_fraction"]),
                "Surface_mode": rr["surface_mode"],
                "Re_water": float(rr["water_ht"]["Re_water"]),
                "Pr_water": float(rr["water_ht"]["Pr_water"]),
                "Tube_velocity_m_s": float(rr["water_ht"]["velocity_m_s"]),
                "Re_air": float(rr["air_corr"]["Re_air"]),
                "Pr_air": float(rr["air_corr"]["Pr_air"]),
                "Air_max_velocity_m_s": float(rr["air_corr"]["u_max_m_s"]),
                "Air_dP_cell_Pa": float(rr["air_dp_Pa"]),
                "UA_dry_W_K": float(rr["UA_dry_W_K"]),
            })
        last = cell_results[route[-1]]
        circ.append({
            "Circuit": int(c),
            "Passes": len(route),
            "Mass_flow_kg_s": flow_by_c[int(c)],
            "Water_out_C": float(last["water_out_C"]),
            "Circuit_Q_kW": qsum,
            "Mean_water_C": float(np.mean(temps)) if temps else np.nan,
        })
    return pd.DataFrame(rows), pd.DataFrame(circ)


def coupled_tube_by_tube_performance(
    g: CoilGeometry,
    air_in_cond: AirCondition,
    air_volume_flow_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_in_C: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    routes: Dict[int, List[str]],
    header_feed_end: str = "Top",
    water_row_progression: str = "Explicit circuit route",
    air_htc_multiplier: float = 1.0,
    air_dp_multiplier: float = 1.0,
    wet_air_dp_factor: float = 1.12,
    air_fouling_m2K_W: float = 0.0,
    water_fouling_m2K_W: float = 0.0,
    max_iter: int = 35,
    tol_K: float = 0.003,
    relaxation: float = 0.62,
    hydraulic_outer_iter: int = 3,
) -> Dict[str, object]:
    """Solve local tube-water and air-lane states to simultaneous convergence.

    A complete physical circuit map is mandatory.  The solver first obtains a row-bank
    solution only as a robust initial guess, then discards the row-uniform tube temperature
    assumption for the reported solution.
    """
    geom = geometry_areas(g)
    tpr = int(geom["n_tubes_per_row"])
    ncircuits = int(hyd.circuits)
    if len(routes) != ncircuits:
        raise ValueError("The physical circuit map must contain every selected circuit.")

    baseline = segmented_thermal_performance(
        g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct, water_in_C,
        water_pressure_Pa, hyd, water_row_progression, air_htc_multiplier,
        air_dp_multiplier, wet_air_dp_factor, air_fouling_m2K_W, water_fouling_m2K_W,
    )
    mean_seed = 0.5 * (water_in_C + float(baseline["water_out_C"]))
    common_props = coolant_props(coolant_kind, glycol_pct, mean_seed, water_pressure_Pa)
    hydraulics = explicit_circuit_hydraulics(routes, geom, hyd, common_props, tpr, header_feed_end)
    flows = list(hydraulics["flows_kg_s"])

    # Seed each tube inlet from the older row-duty-conserving reconstruction.  This generally
    # places the fixed-point iteration close to the coupled solution and dramatically reduces
    # the number of expensive wet-coil cell solves.
    seed = tube_temperature_postprocess(
        routes, baseline["row_table"], tpr, flows, water_in_C, baseline["water_props"]["cp"]
    )
    tw_guess = {str(rr["Tube"]): float(rr["Water_in_C"]) for _, rr in seed["tube_table"].iterrows()}
    for c, route in routes.items():
        if route:
            tw_guess[route[0]] = float(water_in_C)

    thermal_converged = False
    thermal_iterations = 0
    grid = None
    hydraulic_iterations = 1

    for outer in range(1, max(int(hydraulic_outer_iter), 1) + 1):
        hydraulic_iterations = outer
        # Re-seed first tube in each route exactly at the supply temperature.
        for c, route in routes.items():
            tw_guess[route[0]] = float(water_in_C)

        for it in range(1, max_iter + 1):
            grid = _run_grid_iteration(
                g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
                water_pressure_Pa, hyd, routes, flows, tw_guess,
                air_htc_multiplier, air_dp_multiplier, wet_air_dp_factor,
                air_fouling_m2K_W, water_fouling_m2K_W,
            )
            new = grid["new_tw_in"]
            delta = max(abs(float(new[k]) - float(tw_guess[k])) for k in tw_guess)
            thermal_iterations = it
            relaxed = {}
            for k in tw_guess:
                relaxed[k] = (1.0 - relaxation) * float(tw_guess[k]) + relaxation * float(new[k])
            for c, route in routes.items():
                relaxed[route[0]] = float(water_in_C)
            tw_guess = relaxed
            if delta < tol_K:
                thermal_converged = True
                break

        # Final cell evaluation for this hydraulic-flow iteration.
        grid = _run_grid_iteration(
            g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
            water_pressure_Pa, hyd, routes, flows, tw_guess,
            air_htc_multiplier, air_dp_multiplier, wet_air_dp_factor,
            air_fouling_m2K_W, water_fouling_m2K_W,
        )
        cell_table, circuit_table = _assemble_tables(routes, grid["cell_results"], flows)

        # Feed the calculated circuit mean temperatures back into viscosity/density for the
        # explicit hydraulic network.  Header properties use the mixed mean temperature.
        cprops = []
        for c in sorted(routes):
            mT = float(circuit_table.loc[circuit_table["Circuit"] == c, "Mean_water_C"].iloc[0])
            cprops.append(coolant_props(coolant_kind, glycol_pct, mT, water_pressure_Pa))
        mixed_mean = float(np.average(circuit_table["Mean_water_C"], weights=circuit_table["Mass_flow_kg_s"]))
        common_props = coolant_props(coolant_kind, glycol_pct, mixed_mean, water_pressure_Pa)
        new_h = explicit_circuit_hydraulics(
            routes, geom, hyd, common_props, tpr, header_feed_end, circuit_props=cprops
        )
        new_flows = list(new_h["flows_kg_s"])
        fdelta = max(abs(a - b) / max(abs(b), 1e-12) for a, b in zip(new_flows, flows))
        hydraulics = new_h
        if fdelta < 0.002:
            flows = new_flows
            break
        # Under-relax the thermal/hydraulic coupling.
        flows = [0.55 * a + 0.45 * b for a, b in zip(flows, new_flows)]
        scale = hyd.water_mass_flow_kg_s / max(sum(flows), 1e-12)
        flows = [q * scale for q in flows]

    # One final thermal solution at the final hydraulic flow split.
    for c, route in routes.items():
        tw_guess[route[0]] = float(water_in_C)
    for it in range(1, max_iter + 1):
        grid = _run_grid_iteration(
            g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
            water_pressure_Pa, hyd, routes, flows, tw_guess,
            air_htc_multiplier, air_dp_multiplier, wet_air_dp_factor,
            air_fouling_m2K_W, water_fouling_m2K_W,
        )
        new = grid["new_tw_in"]
        delta = max(abs(float(new[k]) - float(tw_guess[k])) for k in tw_guess)
        thermal_iterations = max(thermal_iterations, it)
        tw_guess = {k: (1.0 - relaxation) * float(tw_guess[k]) + relaxation * float(new[k]) for k in tw_guess}
        for c, route in routes.items():
            tw_guess[route[0]] = float(water_in_C)
        if delta < tol_K:
            thermal_converged = True
            break
    grid = _run_grid_iteration(
        g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
        water_pressure_Pa, hyd, routes, flows, tw_guess,
        air_htc_multiplier, air_dp_multiplier, wet_air_dp_factor,
        air_fouling_m2K_W, water_fouling_m2K_W,
    )
    cell_table, circuit_table = _assemble_tables(routes, grid["cell_results"], flows)

    lane_out = [grid["lane_air_out"][t] for t in range(1, tpr + 1)]
    air_out = _mix_air_states(lane_out, [grid["mdot_da_lane"]] * tpr, air_in_cond.pressure_Pa)
    air_in = air_state_from_db_rh(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
    Q_total = float(cell_table["Q_total_kW"].sum())
    Q_sens = float(cell_table["Q_sensible_kW"].sum())
    Q_lat = float(cell_table["Q_latent_kW"].sum())
    mdot_da = float(grid["mdot_da_total"])
    air_Q_check = mdot_da * (air_in["h_J_kgda"] - air_out["h_J_kgda"]) / 1000.0
    energy_err = 100.0 * abs(Q_total - air_Q_check) / max(abs(Q_total), 1e-9)

    circuit_outlet = circuit_table.copy()
    mixed_water_out = float(np.average(circuit_outlet["Water_out_C"], weights=circuit_outlet["Mass_flow_kg_s"]))
    mean_water = 0.5 * (water_in_C + mixed_water_out)
    props_final = coolant_props(coolant_kind, glycol_pct, mean_water, water_pressure_Pa)

    # Update hydraulic network one last time with circuit-specific thermal properties.
    cprops = [
        coolant_props(
            coolant_kind, glycol_pct,
            float(circuit_outlet.loc[circuit_outlet["Circuit"] == c, "Mean_water_C"].iloc[0]),
            water_pressure_Pa,
        ) for c in sorted(routes)
    ]
    hydraulics = explicit_circuit_hydraulics(
        routes, geom, hyd, props_final, tpr, header_feed_end, circuit_props=cprops
    )

    # Reconcile the circuit outlet table with the final hydraulic flow split used for dP.
    # The thermal solution used a nearly identical converged split; report its actual thermal
    # flows in the temperature table and the final pressure-network flows in hydraulics.
    circuit_temp = {
        "tube_table": cell_table,
        "circuit_outlet_table": circuit_outlet,
        "mixed_outlet_C": mixed_water_out,
        "method_note": (
            "Fully coupled 2-D tube-by-tube thermal solve: each tube's local entering coolant "
            "temperature and circuit flow are fed into its local cross-flow wet/dry heat-transfer "
            "calculation; its leaving air feeds the next row in the same air lane and its leaving "
            "coolant feeds the next tube in the routed circuit."
        ),
    }

    row_table = grid["row_table"]
    row_air_dp = float(row_table["Air_dP_Pa"].sum())
    wet_fraction = float(cell_table["Wet_fraction_pct"].mean() / 100.0)
    modes = set(cell_table["Surface_mode"])
    if modes == {"Dry"}:
        surface = "Dry by tube"
    elif modes == {"Fully wet"}:
        surface = "Fully wet by tube"
    else:
        surface = "Mixed / partially wet by tube"

    UA_total = float(cell_table["UA_dry_W_K"].sum())
    C_air = mdot_da * air_in["cp_da"]
    C_water = hyd.water_mass_flow_kg_s * props_final["cp"]
    Cmin, Cmax = min(C_air, C_water), max(C_air, C_water)
    Cr = Cmin / max(Cmax, 1e-12)
    NTU = UA_total / max(Cmin, 1e-12)
    eps_dry = crossflow_effectiveness(NTU, Cr, Cmin_is_water=(C_water <= C_air))
    eps_T = (air_in["T_C"] - air_out["T_C"]) / max(air_in["T_C"] - water_in_C, 1e-12)
    hsat = saturation_enthalpy(water_in_C, air_in_cond.pressure_Pa)
    eps_h = (air_in["h_J_kgda"] - air_out["h_J_kgda"]) / max(air_in["h_J_kgda"] - hsat, 1e-12)

    # Retain the whole-coil resistance decomposition from the established full-geometry
    # calculation.  Local U values are fully coupled above; this diagnostic merely identifies
    # which aggregate resistance family dominates.
    Rair = max(float(baseline.get("resistance_split_pct", {}).get("air", 0.0)), 0.0)
    Rwater = max(float(baseline.get("resistance_split_pct", {}).get("water", 0.0)), 0.0)
    Rwall = max(float(baseline.get("resistance_split_pct", {}).get("wall", 0.0)), 0.0)
    resistance_pct = {"air": Rair, "water": Rwater, "wall": Rwall}

    # Actual tube velocities and Reynolds numbers now vary by circuit.
    htab = hydraulics["table"]
    avg_v = float(np.average(htab["Tube_velocity_m_s"], weights=htab["Mass_flow_kg_s"]))
    avg_Re = float(np.average(htab["Re"], weights=htab["Mass_flow_kg_s"]))
    avg_Pr = float(np.average(cell_table["Pr_water"], weights=cell_table["Circuit_mass_flow_kg_s"]))
    water_ht = dict(baseline["water_ht"])
    water_ht.update({
        "velocity_m_s": avg_v,
        "velocity_min_m_s": float(htab["Tube_velocity_m_s"].min()),
        "velocity_max_m_s": float(htab["Tube_velocity_m_s"].max()),
        "Re_water": avg_Re,
        "Re_water_min": float(htab["Re"].min()),
        "Re_water_max": float(htab["Re"].max()),
        "Pr_water": avg_Pr,
        "mdot_per_circuit_kg_s": float(np.mean(flows)),
    })

    air_corr = dict(baseline["air_corr"])
    air_corr.update({
        "Re_air": float(cell_table["Re_air"].mean()),
        "Pr_air": float(cell_table["Pr_air"].mean()),
        "u_max_m_s": float(cell_table["Air_max_velocity_m_s"].max()),
    })

    condensate = mdot_da * max(air_in["W"] - air_out["W"], 0.0) * 3600.0
    SHR = min(max(Q_sens / max(Q_total, 1e-12), 0.0), 1.0)
    capacity_limiting = "Coolant side" if C_water < C_air else "Air side"
    resistance_limiting = max(resistance_pct, key=resistance_pct.get).capitalize() + " side"

    result = dict(baseline)
    result.update({
        "geometry": geom,
        "air_in": air_in,
        "air_out": air_out,
        "air_corr": air_corr,
        "water_props": props_final,
        "water_ht": water_ht,
        "hydraulics": hydraulics,
        "hydraulics_equal_flow_reference": baseline.get("hydraulics"),
        "Q_total_kW": Q_total,
        "Q_sensible_kW": Q_sens,
        "Q_latent_kW": max(Q_lat, 0.0),
        "SHR": SHR,
        "water_out_C": mixed_water_out,
        "condensate_kg_h": condensate,
        "wet_fraction": wet_fraction,
        "surface_mode": surface,
        "air_dp_Pa": row_air_dp,
        "UA_dry_W_K": UA_total,
        "mdot_da_kg_s": mdot_da,
        "row_table": row_table,
        "cell_table": cell_table,
        "circuit_temperature": circuit_temp,
        "tube2d_converged": thermal_converged,
        "tube2d_iterations": thermal_iterations,
        "tube2d_hydraulic_outer_iterations": hydraulic_iterations,
        "row_march_converged": thermal_converged,
        "row_march_iterations": thermal_iterations,
        "thermal_model": "Fully coupled 2-D tube-by-tube circuit / air-lane model",
        "physical_flow_geometry": "Cross-flow: air is perpendicular to tube/coolant flow",
        "water_row_progression": "Explicit physical circuit routes determine local coolant progression",
        "face_velocity_m_s": air_volume_flow_m3_s / max(geom["face_area_m2"], 1e-12),
        "max_air_velocity_m_s": float(cell_table["Air_max_velocity_m_s"].max()),
        "C_air_kW_K": C_air / 1000.0,
        "C_coolant_kW_K": C_water / 1000.0,
        "Cmin_kW_K": Cmin / 1000.0,
        "Cmax_kW_K": Cmax / 1000.0,
        "Cr": Cr,
        "NTU_dry": NTU,
        "effectiveness_dry_crossflow": eps_dry,
        "wet_enthalpy_effectiveness": min(max(eps_h, 0.0), 1.5),
        "air_temperature_effectiveness": min(max(eps_T, 0.0), 1.5),
        "capacity_rate_limiting_side": capacity_limiting,
        "resistance_limiting_side": resistance_limiting,
        "resistance_split_pct": resistance_pct,
        "energy_balance_error_pct": energy_err,
        "air_lane_assumption": "Equal entering dry-air mass flow per vertical tube lane; no lateral air redistribution",
        "cross_fin_conduction_included": False,
    })
    return result
