"""Core engineering calculations for a fin-and-tube chilled-water cooling coil.

Scope
-----
* Round smooth tubes, continuous plate/wavy fins.
* Water / aqueous ethylene glycol / aqueous propylene glycol.
* Dry, partially-wet and fully-wet cooling/dehumidification.
* Air-side Wang-Tsai-Lu style wavy/louvered j/f correlation (as documented by ACHP).
* Water-side Gnielinski heat transfer and Darcy-Weisbach pressure drop.
* Circuit tube pressure drop + distributed supply/return header pressure drop.

This is an engineering design model, not an AHRI-certified rating program. Final production
selection should be calibrated/validated against coil test data for the actual fin tooling,
collars, return bends, header takeoffs and circuit arrangement.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple
import math

import numpy as np
import pandas as pd
try:
    from CoolProp.CoolProp import PropsSI, HAPropsSI
    HAS_COOLPROP = True
except Exception:
    PropsSI = HAPropsSI = None
    HAS_COOLPROP = False

P_ATM = 101325.0
INCH = 0.0254
MM = 1e-3


@dataclass
class CoilGeometry:
    face_width_m: float
    face_height_m: float
    rows: int
    transverse_pitch_m: float       # Pt: perpendicular to air flow, usually vertical pitch
    longitudinal_pitch_m: float     # Pl: row-to-row pitch in air-flow direction
    tube_od_m: float
    tube_thickness_m: float
    fpi: float
    fin_thickness_m: float
    fin_k_W_mK: float = 205.0
    tube_k_W_mK: float = 380.0
    wave_amplitude_2x_m: float = 1.0e-3  # Pd, twice wave amplitude
    wave_half_period_m: float = 1.0e-3   # xf, half wavelength


@dataclass
class AirCondition:
    db_C: float
    rh_pct: float
    pressure_Pa: float = P_ATM


@dataclass
class HydraulicInputs:
    circuits: int
    water_mass_flow_kg_s: float
    inlet_header_od_m: float
    inlet_header_thickness_m: float
    outlet_header_od_m: float
    outlet_header_thickness_m: float
    header_length_m: float
    header_arrangement: str = "Opposite-end (reverse-return tendency)"
    tube_roughness_m: float = 1.5e-6
    header_roughness_m: float = 1.5e-6
    return_bend_K: float = 1.5
    branch_takeoff_K: float = 0.5
    common_entry_K: float = 0.5
    common_exit_K: float = 1.0


# ---------- Psychrometrics ----------
def _need_coolprop():
    if not HAS_COOLPROP:
        raise RuntimeError("CoolProp is required for psychrometrics and coolant properties. Install requirements.txt.")


def air_state_from_db_rh(db_C: float, rh_pct: float, P: float = P_ATM) -> Dict[str, float]:
    _need_coolprop()
    T = db_C + 273.15
    R = max(0.0001, min(0.9999, rh_pct / 100.0))
    W = HAPropsSI("W", "T", T, "P", P, "R", R)
    h = HAPropsSI("H", "T", T, "P", P, "W", W)  # J/kg dry air
    Tdp = HAPropsSI("D", "T", T, "P", P, "W", W) - 273.15
    Twb = HAPropsSI("B", "T", T, "P", P, "W", W) - 273.15
    # Vda is m3 humid air / kg dry air in CoolProp humid-air interface
    try:
        Vda = HAPropsSI("Vda", "T", T, "P", P, "W", W)
    except Exception:
        # ideal-gas backup, m3/kg dry air
        Vda = 287.055 * T * (1.0 + 1.6078 * W) / P
    rho_ha = (1.0 + W) / max(Vda, 1e-12)
    cp_da = 1006.0 + W * 1860.0
    return dict(T_C=db_C, RH_pct=rh_pct, W=W, h_J_kgda=h, Tdp_C=Tdp, Twb_C=Twb,
                rho_ha=rho_ha, Vda_m3_kgda=Vda, cp_da=cp_da)


def air_state_from_T_W(db_C: float, W: float, P: float = P_ATM) -> Dict[str, float]:
    _need_coolprop()
    T = db_C + 273.15
    W = max(W, 1e-9)
    h = HAPropsSI("H", "T", T, "P", P, "W", W)
    RH = HAPropsSI("R", "T", T, "P", P, "W", W) * 100.0
    Tdp = HAPropsSI("D", "T", T, "P", P, "W", W) - 273.15
    Twb = HAPropsSI("B", "T", T, "P", P, "W", W) - 273.15
    return dict(T_C=db_C, RH_pct=RH, W=W, h_J_kgda=h, Tdp_C=Tdp, Twb_C=Twb,
                cp_da=1006.0 + W * 1860.0)


def T_from_h_W(h_J_kgda: float, W: float, P: float = P_ATM) -> float:
    return HAPropsSI("T", "H", h_J_kgda, "P", P, "W", max(W, 1e-9)) - 273.15


def W_from_T_h(T_C: float, h_J_kgda: float, P: float = P_ATM) -> float:
    return HAPropsSI("W", "T", T_C + 273.15, "P", P, "H", h_J_kgda)


def saturation_enthalpy(T_C: float, P: float = P_ATM) -> float:
    return HAPropsSI("H", "T", T_C + 273.15, "P", P, "R", 1.0)


def saturation_cp(T_C: float, P: float = P_ATM) -> float:
    dT = 0.05
    return (saturation_enthalpy(T_C + dT, P) - saturation_enthalpy(T_C - dT, P)) / (2.0 * dT)


# ---------- Fluid properties ----------
def coolant_string(kind: str, glycol_pct: float) -> str:
    if kind == "Water":
        return "Water"
    pct = int(round(max(0.0, min(60.0, glycol_pct))))
    if kind == "Ethylene Glycol":
        return f"INCOMP::MEG-{pct}%"
    if kind == "Propylene Glycol":
        return f"INCOMP::MPG-{pct}%"
    raise ValueError(f"Unsupported coolant: {kind}")


def coolant_props(kind: str, glycol_pct: float, T_C: float, P: float = 300000.0) -> Dict[str, float]:
    _need_coolprop()
    fluid = coolant_string(kind, glycol_pct)
    T = T_C + 273.15
    return {
        "fluid": fluid,
        "rho": PropsSI("D", "T", T, "P", P, fluid),
        "mu": PropsSI("V", "T", T, "P", P, fluid),
        "cp": PropsSI("C", "T", T, "P", P, fluid),
        "k": PropsSI("L", "T", T, "P", P, fluid),
    }


# ---------- Geometry ----------
def geometry_areas(g: CoilGeometry) -> Dict[str, float]:
    if g.tube_thickness_m * 2 >= g.tube_od_m:
        raise ValueError("Tube wall thickness is too large for the selected OD.")
    if g.rows < 1:
        raise ValueError("Rows must be at least 1.")

    Di = g.tube_od_m - 2.0 * g.tube_thickness_m
    L_tube = g.face_width_m
    Pt = g.transverse_pitch_m
    Pl = g.longitudinal_pitch_m
    fin_pitch = INCH / g.fpi
    n_fins = max(int(math.floor(L_tube / fin_pitch)), 1)
    n_tubes_per_row = max(int(math.floor(g.face_height_m / Pt)), 1)
    n_tubes_total = n_tubes_per_row * g.rows
    depth = Pl * g.rows
    A_face = g.face_width_m * g.face_height_m

    # ACHP-style wavy-fin area enhancement. Pd is twice amplitude; xf is half-period.
    sec_theta = math.sqrt(g.wave_half_period_m ** 2 + g.wave_amplitude_2x_m ** 2) / max(g.wave_half_period_m, 1e-12)

    # Minimum free-flow area: corrected orientation (fins counted along tube length, tubes per row by face height).
    A_c = (
        A_face
        - g.fin_thickness_m * n_fins * (g.face_height_m - g.tube_od_m * n_tubes_per_row)
        - n_tubes_per_row * g.tube_od_m * L_tube
    )
    A_c = max(A_c, 0.02 * A_face)

    A_tube_outer_full = n_tubes_total * math.pi * g.tube_od_m * L_tube
    A_one_fin = 2.0 * (
        g.face_height_m * Pl * g.rows * sec_theta
        - n_tubes_per_row * g.rows * math.pi * g.tube_od_m ** 2 / 4.0
    )
    A_fin = max(n_fins * A_one_fin, 0.0)
    exposed_tube_length = max(L_tube - n_fins * g.fin_thickness_m, 0.0)
    A_bare = n_tubes_total * math.pi * g.tube_od_m * exposed_tube_length
    A_air_total = A_fin + A_bare
    A_i_total = n_tubes_total * math.pi * Di * L_tube
    L_total = n_tubes_total * L_tube

    return {
        "Di_m": Di,
        "tube_length_m": L_tube,
        "n_fins": n_fins,
        "n_tubes_per_row": n_tubes_per_row,
        "n_tubes_total": n_tubes_total,
        "depth_m": depth,
        "face_area_m2": A_face,
        "free_flow_area_m2": A_c,
        "free_area_ratio": A_c / A_face,
        "A_tube_outer_full_m2": A_tube_outer_full,
        "A_fin_m2": A_fin,
        "A_bare_m2": A_bare,
        "A_air_total_m2": A_air_total,
        "A_i_total_m2": A_i_total,
        "L_total_tube_m": L_total,
        "fin_pitch_m": fin_pitch,
        "sec_theta": sec_theta,
    }


# ---------- Air-side correlation ----------
def dry_air_transport(T_C: float, P: float = P_ATM) -> Tuple[float, float]:
    T = T_C + 273.15
    mu = PropsSI("V", "T", T, "P", P, "Air")
    k = PropsSI("L", "T", T, "P", P, "Air")
    return mu, k


def airside_wang_wavy_louvered(
    geom: Dict[str, float], g: CoilGeometry, air_in: Dict[str, float], Vdot_m3_s: float,
    air_htc_multiplier: float = 1.0, air_dp_multiplier: float = 1.0,
) -> Dict[str, float]:
    rho = air_in["rho_ha"]
    mdot_ha = rho * Vdot_m3_s
    A_c = geom["free_flow_area_m2"]
    u_max = mdot_ha / max(rho * A_c, 1e-12)
    mu, k = dry_air_transport(air_in["T_C"], P_ATM)
    cp_ha = (1006.0 + air_in["W"] * 1860.0) / max(1.0 + air_in["W"], 1e-12)  # J/kg humid-air-K
    Pr = cp_ha * mu / max(k, 1e-12)
    Re_D = rho * u_max * g.tube_od_m / max(mu, 1e-12)

    # In the Wang/ACHP equations p_f is fin pitch.
    pf_D = geom["fin_pitch_m"] / g.tube_od_m
    area_ratio = geom["A_air_total_m2"] / max(geom["A_tube_outer_full_m2"], 1e-12)
    Re_eff = max(Re_D, 50.0)
    j = (
        16.06
        * Re_eff ** (-1.02 * pf_D - 0.256)
        * area_ratio ** (-0.601)
        * g.rows ** (-0.069)
        * pf_D ** 0.84
    )
    h_a = j * rho * u_max * cp_ha / max(Pr ** (2.0 / 3.0), 1e-12)

    if Re_eff < 1000.0:
        f = (
            0.264 * (0.105 + 0.708 * math.exp(-Re_eff / 225.0))
            * Re_eff ** (-0.637) * area_ratio ** 0.263 * pf_D ** (-0.317)
        )
    else:
        f = (
            0.768 * (0.0494 + 0.142 * math.exp(-Re_eff / 1180.0))
            * area_ratio ** 0.0195 * pf_D ** (-0.121)
        )
    G_c = mdot_ha / max(A_c, 1e-12)
    dp = area_ratio * G_c * G_c / (2.0 * rho) * f

    return {
        "h_air_W_m2K": h_a * air_htc_multiplier,
        "dp_air_dry_Pa": dp * air_dp_multiplier,
        "j": j,
        "f_air": f,
        "Re_air": Re_D,
        "Pr_air": Pr,
        "u_max_m_s": u_max,
        "mdot_ha_kg_s": mdot_ha,
    }


def fin_efficiency_staggered(g: CoilGeometry, h_a: float, cs_cp: float = 1.0) -> float:
    r = g.tube_od_m / 2.0
    X_D = math.sqrt(g.longitudinal_pitch_m ** 2 + g.transverse_pitch_m ** 2 / 4.0) / 2.0
    X_T = g.transverse_pitch_m / 2.0
    rf_r = 1.27 * (X_T / r) * math.sqrt(max(X_D / X_T - 0.3, 1e-6))
    rf_r = max(rf_r, 1.001)
    phi = (rf_r - 1.0) * (1.0 + 0.35 * math.log(rf_r))
    m = math.sqrt(max(2.0 * h_a * cs_cp / max(g.fin_k_W_mK * g.fin_thickness_m, 1e-12), 0.0))
    X = m * r * phi
    if X < 1e-8:
        return 1.0
    return max(0.05, min(1.0, math.tanh(X) / X))


def overall_surface_efficiency(geom: Dict[str, float], eta_fin: float) -> float:
    return 1.0 - geom["A_fin_m2"] / max(geom["A_air_total_m2"], 1e-12) * (1.0 - eta_fin)


# ---------- Water-side heat transfer / pressure drop ----------
def churchill_friction_factor(Re: float, rel_rough: float) -> float:
    Re = max(Re, 1e-9)
    if Re < 2300:
        return 64.0 / Re
    A = (2.457 * math.log(1.0 / (((7.0 / Re) ** 0.9) + 0.27 * rel_rough))) ** 16
    B = (37530.0 / Re) ** 16
    return 8.0 * ((8.0 / Re) ** 12 + 1.0 / (A + B) ** 1.5) ** (1.0 / 12.0)


def gnielinski_Nu(Re: float, Pr: float, f_darcy: float) -> float:
    if Re <= 2300.0:
        return 3.66
    def turbulent(re: float) -> float:
        num = (f_darcy / 8.0) * (re - 1000.0) * Pr
        den = 1.0 + 12.7 * math.sqrt(f_darcy / 8.0) * (Pr ** (2.0 / 3.0) - 1.0)
        return max(num / max(den, 1e-12), 3.66)
    if Re >= 3000.0:
        return turbulent(Re)
    # transition interpolation to avoid a discontinuity
    w = (Re - 2300.0) / 700.0
    return (1.0 - w) * 3.66 + w * turbulent(3000.0)


def water_side_htc(
    geom: Dict[str, float], circuits: int, mdot_total: float, props: Dict[str, float], roughness_m: float
) -> Dict[str, float]:
    Di = geom["Di_m"]
    Aflow = math.pi * Di ** 2 / 4.0
    mdot_c = mdot_total / max(circuits, 1)
    v = mdot_c / max(props["rho"] * Aflow, 1e-12)
    Re = props["rho"] * v * Di / max(props["mu"], 1e-12)
    Pr = props["cp"] * props["mu"] / max(props["k"], 1e-12)
    f = churchill_friction_factor(Re, roughness_m / Di)
    Nu = gnielinski_Nu(Re, Pr, f)
    h = Nu * props["k"] / Di
    return dict(h_water_W_m2K=h, velocity_m_s=v, Re_water=Re, Pr_water=Pr, f_water=f,
                mdot_per_circuit_kg_s=mdot_c)


def tube_circuit_counts(n_tubes_total: int, circuits: int) -> List[int]:
    circuits = max(1, int(circuits))
    base = n_tubes_total // circuits
    rem = n_tubes_total % circuits
    return [base + (1 if i < rem else 0) for i in range(circuits)]


def straight_plus_bend_dp(
    tube_count: int, tube_length_m: float, Di: float, v: float, rho: float, f: float, return_bend_K: float
) -> float:
    L = tube_count * tube_length_m
    n_bends = max(tube_count - 1, 0)
    dyn = 0.5 * rho * v * v
    return f * (L / Di) * dyn + n_bends * return_bend_K * dyn


def pipe_segment_dp(mdot: float, rho: float, mu: float, D: float, L: float, roughness_m: float) -> float:
    if mdot <= 0 or L <= 0:
        return 0.0
    A = math.pi * D ** 2 / 4.0
    v = mdot / (rho * A)
    Re = rho * v * D / max(mu, 1e-12)
    f = churchill_friction_factor(Re, roughness_m / D)
    return f * L / D * 0.5 * rho * v * v


def distributed_header_paths(
    N: int, mdot_total: float, rho: float, mu: float,
    D_supply: float, D_return: float, L_header: float, roughness_m: float,
    arrangement: str,
) -> List[float]:
    """Header friction contribution for each equal-flow circuit branch.

    Branches are equally spaced. Supply inlet is at the near end. Return outlet can be at the
    near end (same-end) or far end (opposite-end). This is an equal-flow diagnostic; actual
    maldistribution requires a network solver and measured tee-loss coefficients.
    """
    N = max(int(N), 1)
    branch = mdot_total / N
    dx = L_header / N
    supply_seg = []
    return_same_seg = []
    return_opp_seg = []
    for j in range(N):
        mdot_supply = mdot_total - j * branch
        supply_seg.append(pipe_segment_dp(mdot_supply, rho, mu, D_supply, dx, roughness_m))
        # segment j measured from near end. Same-end return also carries flow from branches j..N-1
        mdot_ret_same = mdot_total - j * branch
        return_same_seg.append(pipe_segment_dp(mdot_ret_same, rho, mu, D_return, dx, roughness_m))
        # opposite-end return segment j carries flow accumulated from branches 0..j
        mdot_ret_opp = (j + 1) * branch
        return_opp_seg.append(pipe_segment_dp(mdot_ret_opp, rho, mu, D_return, dx, roughness_m))

    paths = []
    opposite = arrangement.startswith("Opposite")
    for i in range(N):
        dp_s = sum(supply_seg[: i + 1])
        if opposite:
            dp_r = sum(return_opp_seg[i:])
        else:
            dp_r = sum(return_same_seg[: i + 1])
        paths.append(dp_s + dp_r)
    return paths


def water_pressure_drop(
    geom: Dict[str, float], hyd: HydraulicInputs, props: Dict[str, float], water_ht: Dict[str, float]
) -> Dict[str, object]:
    N = hyd.circuits
    Di = geom["Di_m"]
    counts = tube_circuit_counts(geom["n_tubes_total"], N)
    core_dps = [
        straight_plus_bend_dp(c, geom["tube_length_m"], Di, water_ht["velocity_m_s"],
                              props["rho"], water_ht["f_water"], hyd.return_bend_K)
        for c in counts
    ]

    Dsup = hyd.inlet_header_od_m - 2.0 * hyd.inlet_header_thickness_m
    Dret = hyd.outlet_header_od_m - 2.0 * hyd.outlet_header_thickness_m
    if Dsup <= 0 or Dret <= 0:
        raise ValueError("Header thickness must be less than half of header OD.")

    hdr_paths = distributed_header_paths(
        N, hyd.water_mass_flow_kg_s, props["rho"], props["mu"], Dsup, Dret,
        hyd.header_length_m, hyd.header_roughness_m, hyd.header_arrangement,
    )
    As = math.pi * Dsup ** 2 / 4.0
    Ar = math.pi * Dret ** 2 / 4.0
    v_sup = hyd.water_mass_flow_kg_s / (props["rho"] * As)
    v_ret = hyd.water_mass_flow_kg_s / (props["rho"] * Ar)
    common = hyd.common_entry_K * 0.5 * props["rho"] * v_sup ** 2 + hyd.common_exit_K * 0.5 * props["rho"] * v_ret ** 2
    branch_dyn = 0.5 * props["rho"] * water_ht["velocity_m_s"] ** 2
    branch_minor = hyd.branch_takeoff_K * branch_dyn
    total_paths = [core_dps[i] + hdr_paths[i] + common + branch_minor for i in range(N)]

    table = pd.DataFrame({
        "Circuit": np.arange(1, N + 1),
        "Tubes": counts,
        "Core_dP_kPa": np.array(core_dps) / 1000.0,
        "Header_path_dP_kPa": np.array(hdr_paths) / 1000.0,
        "Total_path_dP_kPa": np.array(total_paths) / 1000.0,
    })
    return {
        "table": table,
        "dp_core_avg_kPa": float(np.mean(core_dps) / 1000.0),
        "dp_total_avg_kPa": float(np.mean(total_paths) / 1000.0),
        "dp_total_min_kPa": float(np.min(total_paths) / 1000.0),
        "dp_total_max_kPa": float(np.max(total_paths) / 1000.0),
        "header_path_spread_kPa": float((np.max(total_paths) - np.min(total_paths)) / 1000.0),
        "header_supply_ID_mm": Dsup * 1000.0,
        "header_return_ID_mm": Dret * 1000.0,
        "header_supply_velocity_m_s": v_sup,
        "header_return_velocity_m_s": v_ret,
    }


# ---------- Wet/dry thermal model ----------
def crossflow_effectiveness(NTU: float, Cr: float, Cmin_is_water: bool) -> float:
    Cr = max(min(Cr, 0.999999), 1e-9)
    NTU = max(NTU, 0.0)
    if Cmin_is_water:
        # Cmax air side (unmixed) form used in ACHP DryWetSegment
        return 1.0 - math.exp(-(1.0 / Cr) * (1.0 - math.exp(-Cr * NTU)))
    return (1.0 / Cr) * (1.0 - math.exp(-Cr * (1.0 - math.exp(-NTU))))


def thermal_performance(
    g: CoilGeometry,
    air_in_cond: AirCondition,
    air_volume_flow_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_in_C: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    air_htc_multiplier: float = 1.0,
    air_dp_multiplier: float = 1.0,
    wet_air_dp_factor: float = 1.12,
    air_fouling_m2K_W: float = 0.0,
    water_fouling_m2K_W: float = 0.0,
) -> Dict[str, object]:
    geom = geometry_areas(g)
    ain = air_state_from_db_rh(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
    mdot_da = air_volume_flow_m3_s / ain["Vda_m3_kgda"]
    aircorr = airside_wang_wavy_louvered(geom, g, ain, air_volume_flow_m3_s,
                                         air_htc_multiplier, air_dp_multiplier)

    # Initial coolant properties at inlet; update cp with mean temperature after first solve.
    props = coolant_props(coolant_kind, glycol_pct, water_in_C, water_pressure_Pa)
    water_ht = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props, hyd.tube_roughness_m)

    h_a = aircorr["h_air_W_m2K"]
    eta_fin_dry = fin_efficiency_staggered(g, h_a, 1.0)
    eta_o_dry = overall_surface_efficiency(geom, eta_fin_dry)

    A_a = geom["A_air_total_m2"]
    A_i = geom["A_i_total_m2"]
    UA_o_dry = eta_o_dry * h_a * A_a
    UA_i = water_ht["h_water_W_m2K"] * A_i

    # Tube wall + fouling as total resistance for the whole coil.
    Ltot = geom["L_total_tube_m"]
    R_wall = math.log(g.tube_od_m / geom["Di_m"]) / (2.0 * math.pi * g.tube_k_W_mK * Ltot)
    R_fo = air_fouling_m2K_W / max(A_a, 1e-12)
    R_fi = water_fouling_m2K_W / max(A_i, 1e-12)
    R_inside = 1.0 / max(UA_i, 1e-12) + R_wall + R_fi
    UA_i_eff = 1.0 / max(R_inside, 1e-12)
    R_outside_dry = 1.0 / max(UA_o_dry, 1e-12) + R_fo
    UA_o_eff_dry = 1.0 / max(R_outside_dry, 1e-12)
    UA_dry = 1.0 / (1.0 / UA_i_eff + 1.0 / UA_o_eff_dry)

    C_air = mdot_da * ain["cp_da"]
    C_w = hyd.water_mass_flow_kg_s * props["cp"]
    Cmin, Cmax = min(C_air, C_w), max(C_air, C_w)
    Cr = Cmin / max(Cmax, 1e-12)
    NTU = UA_dry / max(Cmin, 1e-12)
    eps = crossflow_effectiveness(NTU, Cr, Cmin_is_water=(C_w <= C_air))
    Q_dry = max(0.0, eps * Cmin * (ain["T_C"] - water_in_C))
    Tout_air_dry = ain["T_C"] - Q_dry / max(C_air, 1e-12)
    Tout_w_dry = water_in_C + Q_dry / max(C_w, 1e-12)

    # Surface endpoint estimates for dry/wet decision.
    T_surface_air_out = (UA_o_eff_dry * Tout_air_dry + UA_i_eff * water_in_C) / max(UA_o_eff_dry + UA_i_eff, 1e-12)
    T_surface_air_in = (UA_o_eff_dry * ain["T_C"] + UA_i_eff * Tout_w_dry) / max(UA_o_eff_dry + UA_i_eff, 1e-12)

    f_dry = 1.0
    Q_total = Q_dry
    Q_sensible = Q_dry
    Tout_air = Tout_air_dry
    Wout = ain["W"]
    Tout_w = Tout_w_dry
    wet_mode = "Dry"

    if T_surface_air_out < ain["Tdp_C"]:
        # Wet-surface enthalpy-potential calculation, based on the single-phase dry/wet
        # approach used in ACHP/EnergyPlus-style coil models. Full-wet solution is solved
        # first; a bounded dry-fraction approximation is then used when inlet surface is dry.
        wet_mode = "Fully wet" if T_surface_air_in <= ain["Tdp_C"] else "Partially wet"

        # Iterate mean water temperature because cp and saturation slope vary.
        Tout_w_guess = max(water_in_C + 0.5, min(ain["T_C"] - 0.1, Tout_w_dry))
        for _ in range(40):
            Tmean_w = 0.5 * (water_in_C + Tout_w_guess)
            props_m = coolant_props(coolant_kind, glycol_pct, Tmean_w, water_pressure_Pa)
            water_ht_m = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props_m, hyd.tube_roughness_m)
            UA_i_m = water_ht_m["h_water_W_m2K"] * A_i
            R_inside_m = 1.0 / max(UA_i_m, 1e-12) + R_wall + R_fi
            UA_i_eff_m = 1.0 / max(R_inside_m, 1e-12)
            cs = saturation_cp(Tmean_w, air_in_cond.pressure_Pa)
            cs_cp = max(cs / ain["cp_da"], 1.0)
            eta_fin_wet = fin_efficiency_staggered(g, h_a, cs_cp)
            eta_o_wet = overall_surface_efficiency(geom, eta_fin_wet)
            UA_o_wet_raw = eta_o_wet * h_a * A_a
            UA_o_eff_wet = 1.0 / (1.0 / max(UA_o_wet_raw, 1e-12) + R_fo)

            Ntu_i = UA_i_eff_m / max(hyd.water_mass_flow_kg_s * props_m["cp"], 1e-12)
            Ntu_o = UA_o_eff_wet / max(mdot_da * ain["cp_da"], 1e-12)
            Cw_star = hyd.water_mass_flow_kg_s * props_m["cp"] / max(cs, 1e-12)  # kg_da/s equivalent
            m_star = min(Cw_star, mdot_da) / max(Cw_star, mdot_da)
            mdot_min_eq = min(Cw_star, mdot_da)
            Ntu_owet = Ntu_o
            if hyd.water_mass_flow_kg_s * props_m["cp"] > cs * mdot_da:
                Ntu_wet = Ntu_o / max(1.0 + m_star * (Ntu_owet / max(Ntu_i, 1e-12)), 1e-12)
            else:
                Ntu_wet = Ntu_i / max(1.0 + m_star * (Ntu_i / max(Ntu_owet, 1e-12)), 1e-12)
            if abs(1.0 - m_star) < 1e-7:
                eps_wet = Ntu_wet / (1.0 + Ntu_wet)
            else:
                ex = math.exp(-Ntu_wet * (1.0 - m_star))
                eps_wet = (1.0 - ex) / max(1.0 - m_star * ex, 1e-12)

            hsat_wi = saturation_enthalpy(water_in_C, air_in_cond.pressure_Pa)
            Q_full_wet = max(0.0, eps_wet * mdot_min_eq * (ain["h_J_kgda"] - hsat_wi))
            Tout_w_new = water_in_C + Q_full_wet / max(hyd.water_mass_flow_kg_s * props_m["cp"], 1e-12)
            if abs(Tout_w_new - Tout_w_guess) < 1e-5:
                Tout_w_guess = Tout_w_new
                props = props_m
                water_ht = water_ht_m
                break
            Tout_w_guess = 0.5 * Tout_w_guess + 0.5 * Tout_w_new
            props = props_m
            water_ht = water_ht_m

        # Estimate dry fraction from inlet dry-surface crossing; clipped for stability.
        if wet_mode == "Partially wet":
            # ACHP two-phase interface relation is a useful bounded approximation here.
            T_ac = ain["Tdp_C"] + UA_i_eff / max(UA_o_eff_dry, 1e-12) * (ain["Tdp_C"] - water_in_C)
            eps_dry_part = (ain["T_C"] - T_ac) / max(ain["T_C"] - water_in_C, 1e-12)
            eps_dry_part = min(max(eps_dry_part, 0.0), 0.999999)
            Ntu_dry_air = UA_dry / max(C_air, 1e-12)
            f_dry = min(max(-math.log(max(1.0 - eps_dry_part, 1e-12)) / max(Ntu_dry_air, 1e-12), 0.0), 1.0)
        else:
            f_dry = 0.0

        # Blend dry section and wet section in air-flow sequence.
        Q_dry_part = f_dry * Q_dry
        h_after_dry = ain["h_J_kgda"] - Q_dry_part / max(mdot_da, 1e-12)
        T_after_dry = ain["T_C"] - Q_dry_part / max(C_air, 1e-12)
        W_after_dry = ain["W"]

        wet_fraction = 1.0 - f_dry
        Q_wet = wet_fraction * Q_full_wet
        Q_total = Q_dry_part + Q_wet
        hout = ain["h_J_kgda"] - Q_total / max(mdot_da, 1e-12)
        Tout_w = water_in_C + Q_total / max(hyd.water_mass_flow_kg_s * props["cp"], 1e-12)

        # Effective saturated surface enthalpy and DB for wet portion.
        if wet_fraction > 1e-6 and Ntu_o > 1e-9:
            denom = 1.0 - math.exp(-wet_fraction * Ntu_o)
            h_surf_eff = h_after_dry + (hout - h_after_dry) / max(denom, 1e-12)
            # Clamp to a physically valid saturation temperature bracket.
            lo, hi = water_in_C - 2.0, max(T_after_dry, water_in_C + 0.01)
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if saturation_enthalpy(mid, air_in_cond.pressure_Pa) < h_surf_eff:
                    lo = mid
                else:
                    hi = mid
            T_surf_eff = 0.5 * (lo + hi)
            Tout_air = T_surf_eff + (T_after_dry - T_surf_eff) * math.exp(-wet_fraction * Ntu_o)
        else:
            Tout_air = T_after_dry

        try:
            Wout = W_from_T_h(Tout_air, hout, air_in_cond.pressure_Pa)
        except Exception:
            # Use saturated cap if CoolProp inversion has difficulty near the saturation line.
            Wsat_out = HAPropsSI("W", "T", Tout_air + 273.15, "P", air_in_cond.pressure_Pa, "R", 0.9999)
            Wout = min(W_after_dry, Wsat_out)
        Wsat_out = HAPropsSI("W", "T", Tout_air + 273.15, "P", air_in_cond.pressure_Pa, "R", 0.9999)
        Wout = min(ain["W"], Wsat_out, max(Wout, 1e-9))
        Q_sensible = mdot_da * ain["cp_da"] * max(ain["T_C"] - Tout_air, 0.0)

    aout = air_state_from_T_W(Tout_air, Wout, air_in_cond.pressure_Pa)
    condensate_kg_s = mdot_da * max(ain["W"] - Wout, 0.0)
    SHR = min(max(Q_sensible / max(Q_total, 1e-12), 0.0), 1.0)
    wet_fraction = 1.0 - f_dry
    dp_air = aircorr["dp_air_dry_Pa"] * (f_dry + wet_fraction * wet_air_dp_factor)

    # Hydraulics with mean-fluid properties from final thermal solution.
    mean_w = 0.5 * (water_in_C + Tout_w)
    props_final = coolant_props(coolant_kind, glycol_pct, mean_w, water_pressure_Pa)
    water_ht_final = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props_final, hyd.tube_roughness_m)
    hydres = water_pressure_drop(geom, hyd, props_final, water_ht_final)

    return {
        "geometry": geom,
        "air_in": ain,
        "air_out": aout,
        "air_corr": aircorr,
        "water_props": props_final,
        "water_ht": water_ht_final,
        "hydraulics": hydres,
        "Q_total_kW": Q_total / 1000.0,
        "Q_sensible_kW": Q_sensible / 1000.0,
        "Q_latent_kW": max(Q_total - Q_sensible, 0.0) / 1000.0,
        "SHR": SHR,
        "water_out_C": Tout_w,
        "condensate_kg_h": condensate_kg_s * 3600.0,
        "f_dry": f_dry,
        "wet_fraction": wet_fraction,
        "surface_mode": wet_mode,
        "air_dp_Pa": dp_air,
        "eta_fin_dry": eta_fin_dry,
        "eta_o_dry": eta_o_dry,
        "UA_dry_W_K": UA_dry,
        "mdot_da_kg_s": mdot_da,
    }


def target_load(air_in: AirCondition, air_out_db_C: float, air_out_rh_pct: float, Vdot_m3_s: float) -> Dict[str, float]:
    ain = air_state_from_db_rh(air_in.db_C, air_in.rh_pct, air_in.pressure_Pa)
    aout = air_state_from_db_rh(air_out_db_C, air_out_rh_pct, air_in.pressure_Pa)
    mdot_da = Vdot_m3_s / ain["Vda_m3_kgda"]
    Q = mdot_da * (ain["h_J_kgda"] - aout["h_J_kgda"])
    Qs = mdot_da * ain["cp_da"] * (air_in.db_C - air_out_db_C)
    return {"Q_required_kW": Q / 1000.0, "Q_sensible_required_kW": Qs / 1000.0,
            "SHR_required": Qs / max(Q, 1e-12), "mdot_da_kg_s": mdot_da}


def warnings_for_result(result: Dict[str, object]) -> List[str]:
    w = []
    geom = result["geometry"]
    wh = result["water_ht"]
    hyd = result["hydraulics"]
    ac = result["air_corr"]
    if geom["free_area_ratio"] < 0.30:
        w.append("Low free-flow area ratio; re-check fin/tube geometry and expect high air pressure drop.")
    if ac["Re_air"] < 300 or ac["Re_air"] > 8000:
        w.append("Air Reynolds number is outside the approximate 300–8000 range reported for the Wang wavy/louvered data set; correlation extrapolation is occurring.")
    if wh["Re_water"] < 3000:
        w.append("Water-side Reynolds number is below 3000; turbulent Gnielinski performance is not fully established and heat transfer may be transition/laminar.")
    if wh["velocity_m_s"] < 0.45:
        w.append("Low tube water velocity (<0.45 m/s): check fouling risk, air removal and low Reynolds number.")
    if wh["velocity_m_s"] > 2.4:
        w.append("High tube water velocity (>2.4 m/s): check erosion, noise and return-bend/header losses for your tube material and water quality.")
    if hyd["header_supply_velocity_m_s"] > 2.5 or hyd["header_return_velocity_m_s"] > 2.5:
        w.append("Header velocity exceeds 2.5 m/s; consider a larger header ID and check noise/erosion criteria.")
    if hyd["header_path_spread_kPa"] > max(0.15 * hyd["dp_total_avg_kPa"], 2.0):
        w.append("Large calculated circuit-path pressure spread: equal-flow assumption may be poor. Consider opposite-end headers, balancing, or a hydraulic network calculation.")
    if result["air_dp_Pa"] > 300:
        w.append("Air-side coil pressure drop is high (>300 Pa); check face velocity, FPI, rows and wet correction against fan static allowance.")
    return w

# =============================================================================
# v2 segmented / row-marching extensions
# =============================================================================
# Keep the first-generation whole-coil solver available for comparison/validation.
thermal_performance_whole_coil_v1 = thermal_performance


def air_state_from_db_wb(db_C: float, wb_C: float, P: float = P_ATM) -> Dict[str, float]:
    """Humid-air state from dry-bulb and wet-bulb temperature."""
    _need_coolprop()
    if wb_C > db_C:
        raise ValueError("Wet-bulb temperature cannot exceed dry-bulb temperature.")
    T = db_C + 273.15
    B = wb_C + 273.15
    W = HAPropsSI("W", "T", T, "P", P, "B", B)
    return air_state_from_T_W(db_C, W, P)


def _air_state_full_from_T_W(db_C: float, W: float, P: float = P_ATM) -> Dict[str, float]:
    """air_state_from_T_W plus density/specific volume used by segmented calculations."""
    s = air_state_from_T_W(db_C, W, P)
    T = db_C + 273.15
    try:
        Vda = HAPropsSI("Vda", "T", T, "P", P, "W", max(W, 1e-9))
    except Exception:
        Vda = 287.055 * T * (1.0 + 1.6078 * W) / P
    s["Vda_m3_kgda"] = Vda
    s["rho_ha"] = (1.0 + W) / max(Vda, 1e-12)
    return s


def target_load_from_condition(
    air_in: AirCondition,
    Vdot_m3_s: float,
    outlet_db_C: float,
    outlet_value: float,
    outlet_mode: str = "DB + RH",
) -> Dict[str, object]:
    """Build a target from either DB+RH or DB+WB leaving-air conditions."""
    ain = air_state_from_db_rh(air_in.db_C, air_in.rh_pct, air_in.pressure_Pa)
    if outlet_mode == "DB + WB":
        aout = air_state_from_db_wb(outlet_db_C, outlet_value, air_in.pressure_Pa)
    else:
        aout = air_state_from_db_rh(outlet_db_C, outlet_value, air_in.pressure_Pa)
    mdot_da = Vdot_m3_s / ain["Vda_m3_kgda"]
    Q = mdot_da * (ain["h_J_kgda"] - aout["h_J_kgda"])
    Qs = mdot_da * ain["cp_da"] * (air_in.db_C - outlet_db_C)
    return {
        "mode": "Leaving air condition",
        "Q_required_kW": Q / 1000.0,
        "Q_sensible_required_kW": Qs / 1000.0,
        "SHR_required": Qs / max(Q, 1e-12),
        "mdot_da_kg_s": mdot_da,
        "air_target": aout,
        "target_db_C": outlet_db_C,
        "target_W": aout["W"],
        "target_RH_pct": aout["RH_pct"],
        "target_WB_C": aout["Twb_C"],
        "outlet_mode": outlet_mode,
    }


def target_capacity(kW: float) -> Dict[str, object]:
    return {"mode": "Cooling capacity", "Q_required_kW": float(kW)}


def target_is_met(result: Dict[str, object], target: Dict[str, object], temp_tol_K: float = 0.20) -> bool:
    if target.get("mode") == "Leaving air condition":
        t_ok = result["air_out"]["T_C"] <= float(target["target_db_C"]) + temp_tol_K
        # Humidity ratio is the physically robust moisture target; RH alone can rise as air cools.
        w_ok = result["air_out"]["W"] <= float(target["target_W"]) + 2.0e-5
        q_ok = result["Q_total_kW"] >= 0.99 * float(target["Q_required_kW"])
        return bool(t_ok and w_ok and q_ok)
    return bool(result["Q_total_kW"] >= float(target.get("Q_required_kW", 0.0)))


def _solve_saturation_temperature_from_h(h_target: float, P: float, lo_C: float, hi_C: float) -> float:
    lo = min(lo_C, hi_C)
    hi = max(lo_C, hi_C)
    h_lo = saturation_enthalpy(lo, P)
    h_hi = saturation_enthalpy(hi, P)
    if h_target <= h_lo:
        return lo
    if h_target >= h_hi:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if saturation_enthalpy(mid, P) < h_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _row_segment(
    g: CoilGeometry,
    geom: Dict[str, float],
    air_in: Dict[str, float],
    water_in_C: float,
    mdot_da: float,
    coolant_kind: str,
    glycol_pct: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    h_air: float,
    air_fouling_m2K_W: float,
    water_fouling_m2K_W: float,
) -> Dict[str, object]:
    """Solve one bank/row using a dry/wet enthalpy-potential segment model."""
    nr = max(g.rows, 1)
    A_a = geom["A_air_total_m2"] / nr
    A_i = geom["A_i_total_m2"] / nr
    Ltot = geom["L_total_tube_m"] / nr
    cp_air = air_in["cp_da"]
    C_air = mdot_da * cp_air

    # A few inner iterations update fluid properties with row mean temperature.
    Tw_out_guess = water_in_C + 0.25
    last = None
    for _ in range(12):
        Tmean_w = 0.5 * (water_in_C + Tw_out_guess)
        props = coolant_props(coolant_kind, glycol_pct, Tmean_w, water_pressure_Pa)
        wh = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props, hyd.tube_roughness_m)
        C_w = hyd.water_mass_flow_kg_s * props["cp"]

        eta_fin_dry = fin_efficiency_staggered(g, h_air, 1.0)
        eta_o_dry = 1.0 - (geom["A_fin_m2"] / max(geom["A_air_total_m2"], 1e-12)) * (1.0 - eta_fin_dry)
        UA_o_dry_raw = eta_o_dry * h_air * A_a
        UA_i_raw = wh["h_water_W_m2K"] * A_i
        R_wall = math.log(g.tube_od_m / geom["Di_m"]) / (2.0 * math.pi * g.tube_k_W_mK * Ltot)
        R_fo = air_fouling_m2K_W / max(A_a, 1e-12)
        R_fi = water_fouling_m2K_W / max(A_i, 1e-12)
        UA_i_eff = 1.0 / (1.0 / max(UA_i_raw, 1e-12) + R_wall + R_fi)
        UA_o_eff_dry = 1.0 / (1.0 / max(UA_o_dry_raw, 1e-12) + R_fo)
        UA_dry = 1.0 / (1.0 / max(UA_i_eff, 1e-12) + 1.0 / max(UA_o_eff_dry, 1e-12))

        Cmin = min(C_air, C_w)
        Cmax = max(C_air, C_w)
        Cr = Cmin / max(Cmax, 1e-12)
        NTU = UA_dry / max(Cmin, 1e-12)
        eps_dry = crossflow_effectiveness(NTU, Cr, Cmin_is_water=(C_w <= C_air))
        Q_dry = max(0.0, eps_dry * Cmin * (air_in["T_C"] - water_in_C))
        Ta_dry = air_in["T_C"] - Q_dry / max(C_air, 1e-12)
        Tw_dry = water_in_C + Q_dry / max(C_w, 1e-12)

        Tsurf_out = (UA_o_eff_dry * Ta_dry + UA_i_eff * water_in_C) / max(UA_o_eff_dry + UA_i_eff, 1e-12)
        Tsurf_in = (UA_o_eff_dry * air_in["T_C"] + UA_i_eff * Tw_dry) / max(UA_o_eff_dry + UA_i_eff, 1e-12)

        if Tsurf_out >= air_in["Tdp_C"]:
            f_dry = 1.0
            wet_fraction = 0.0
            mode = "Dry"
            Q = Q_dry
            Qs = Q_dry
            Ta_out = Ta_dry
            Wout = air_in["W"]
            Tw_out = Tw_dry
            eps_wet = 0.0
            eta_fin_wet = eta_fin_dry
            UA_o_eff_wet = UA_o_eff_dry
        else:
            mode = "Fully wet" if Tsurf_in <= air_in["Tdp_C"] else "Partially wet"
            cs = saturation_cp(Tmean_w, air_in.get("pressure_Pa", P_ATM))
            cs_cp = max(cs / max(cp_air, 1e-12), 1.0)
            eta_fin_wet = fin_efficiency_staggered(g, h_air, cs_cp)
            eta_o_wet = 1.0 - (geom["A_fin_m2"] / max(geom["A_air_total_m2"], 1e-12)) * (1.0 - eta_fin_wet)
            UA_o_wet_raw = eta_o_wet * h_air * A_a
            UA_o_eff_wet = 1.0 / (1.0 / max(UA_o_wet_raw, 1e-12) + R_fo)

            Ntu_i = UA_i_eff / max(C_w, 1e-12)
            Ntu_o = UA_o_eff_wet / max(C_air, 1e-12)
            Cw_star = C_w / max(cs, 1e-12)  # equivalent dry-air mass capacity rate [kg_da/s]
            m_star = min(Cw_star, mdot_da) / max(Cw_star, mdot_da, 1e-12)
            mdot_min_eq = min(Cw_star, mdot_da)
            if C_w > cs * mdot_da:
                Ntu_wet = Ntu_o / max(1.0 + m_star * (Ntu_o / max(Ntu_i, 1e-12)), 1e-12)
            else:
                Ntu_wet = Ntu_i / max(1.0 + m_star * (Ntu_i / max(Ntu_o, 1e-12)), 1e-12)
            if abs(1.0 - m_star) < 1e-7:
                eps_wet = Ntu_wet / (1.0 + Ntu_wet)
            else:
                ex = math.exp(-Ntu_wet * (1.0 - m_star))
                eps_wet = (1.0 - ex) / max(1.0 - m_star * ex, 1e-12)

            hsat_wi = saturation_enthalpy(water_in_C, air_in.get("pressure_Pa", P_ATM))
            Q_full_wet = max(0.0, eps_wet * mdot_min_eq * (air_in["h_J_kgda"] - hsat_wi))

            if mode == "Partially wet":
                T_ac = air_in["Tdp_C"] + UA_i_eff / max(UA_o_eff_dry, 1e-12) * (air_in["Tdp_C"] - water_in_C)
                eps_part = (air_in["T_C"] - T_ac) / max(air_in["T_C"] - water_in_C, 1e-12)
                eps_part = min(max(eps_part, 0.0), 0.999999)
                Ntu_dry_air = UA_dry / max(C_air, 1e-12)
                f_dry = min(max(-math.log(max(1.0 - eps_part, 1e-12)) / max(Ntu_dry_air, 1e-12), 0.0), 1.0)
            else:
                f_dry = 0.0
            wet_fraction = 1.0 - f_dry

            Q_dry_part = f_dry * Q_dry
            Q_wet = wet_fraction * Q_full_wet
            Q = Q_dry_part + Q_wet
            h_after_dry = air_in["h_J_kgda"] - Q_dry_part / max(mdot_da, 1e-12)
            T_after_dry = air_in["T_C"] - Q_dry_part / max(C_air, 1e-12)
            hout = air_in["h_J_kgda"] - Q / max(mdot_da, 1e-12)
            Tw_out = water_in_C + Q / max(C_w, 1e-12)

            if wet_fraction > 1e-7 and Ntu_o > 1e-10:
                denom = 1.0 - math.exp(-wet_fraction * Ntu_o)
                h_surf_eff = h_after_dry + (hout - h_after_dry) / max(denom, 1e-12)
                T_surf_eff = _solve_saturation_temperature_from_h(
                    h_surf_eff, air_in.get("pressure_Pa", P_ATM), water_in_C - 3.0, max(T_after_dry, water_in_C + 0.01)
                )
                Ta_out = T_surf_eff + (T_after_dry - T_surf_eff) * math.exp(-wet_fraction * Ntu_o)
            else:
                Ta_out = T_after_dry
            try:
                Wout = W_from_T_h(Ta_out, hout, air_in.get("pressure_Pa", P_ATM))
            except Exception:
                Wout = air_in["W"]
            Wsat = HAPropsSI("W", "T", Ta_out + 273.15, "P", air_in.get("pressure_Pa", P_ATM), "R", 0.9999)
            Wout = min(air_in["W"], Wsat, max(Wout, 1e-9))
            Qs = mdot_da * cp_air * max(air_in["T_C"] - Ta_out, 0.0)

        if abs(Tw_out - Tw_out_guess) < 2e-5:
            last = (props, wh, C_w, eta_fin_dry, eta_fin_wet, UA_i_eff, UA_o_eff_dry, UA_o_eff_wet,
                    UA_dry, Cmin, Cmax, Cr, NTU, eps_dry, eps_wet, f_dry, wet_fraction, mode, Q, Qs, Ta_out, Wout, Tw_out)
            break
        Tw_out_guess = 0.5 * Tw_out_guess + 0.5 * Tw_out
        last = (props, wh, C_w, eta_fin_dry, eta_fin_wet, UA_i_eff, UA_o_eff_dry, UA_o_eff_wet,
                UA_dry, Cmin, Cmax, Cr, NTU, eps_dry, eps_wet, f_dry, wet_fraction, mode, Q, Qs, Ta_out, Wout, Tw_out)

    (props, wh, C_w, eta_fin_dry, eta_fin_wet, UA_i_eff, UA_o_eff_dry, UA_o_eff_wet,
     UA_dry, Cmin, Cmax, Cr, NTU, eps_dry, eps_wet, f_dry, wet_fraction, mode, Q, Qs,
     Ta_out, Wout, Tw_out) = last
    aout = _air_state_full_from_T_W(Ta_out, Wout, air_in.get("pressure_Pa", P_ATM))
    return {
        "air_out": aout,
        "water_out_C": Tw_out,
        "Q_W": Q,
        "Q_sensible_W": min(Qs, Q),
        "Q_latent_W": max(Q - Qs, 0.0),
        "wet_fraction": wet_fraction,
        "surface_mode": mode,
        "C_air_W_K": C_air,
        "C_water_W_K": C_w,
        "Cmin_W_K": Cmin,
        "Cmax_W_K": Cmax,
        "Cr": Cr,
        "NTU_dry": NTU,
        "effectiveness_dry": eps_dry,
        "effectiveness_wet_enthalpy": eps_wet,
        "UA_dry_W_K": UA_dry,
        "h_water_W_m2K": wh["h_water_W_m2K"],
        "Re_water": wh["Re_water"],
        "Pr_water": wh["Pr_water"],
        "water_velocity_m_s": wh["velocity_m_s"],
        "eta_fin_dry": eta_fin_dry,
        "eta_fin_wet": eta_fin_wet,
    }


def thermal_performance(
    g: CoilGeometry,
    air_in_cond: AirCondition,
    air_volume_flow_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_in_C: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    air_htc_multiplier: float = 1.0,
    air_dp_multiplier: float = 1.0,
    wet_air_dp_factor: float = 1.12,
    air_fouling_m2K_W: float = 0.0,
    water_fouling_m2K_W: float = 0.0,
    water_thermal_arrangement: str = "Counterflow / water enters air-leaving side",
) -> Dict[str, object]:
    """Segmented chilled-water coil model with row-by-row air and coolant marching.

    The row model is an equivalent bank model. Exact tube-by-tube water temperatures require the
    physical circuit routing map (which tube connects to which return bend/header branch).
    """
    geom = geometry_areas(g)
    if hyd.circuits > geom["n_tubes_total"]:
        raise ValueError(f"Parallel circuits ({hyd.circuits}) cannot exceed total tubes ({geom['n_tubes_total']}).")
    ain = air_state_from_db_rh(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
    ain["pressure_Pa"] = air_in_cond.pressure_Pa
    mdot_da = air_volume_flow_m3_s / ain["Vda_m3_kgda"]
    aircorr = airside_wang_wavy_louvered(geom, g, ain, air_volume_flow_m3_s,
                                         air_htc_multiplier, air_dp_multiplier)
    h_air = aircorr["h_air_W_m2K"]

    # Solve the coupled row temperatures. Counterflow requires fixed-point iteration because
    # air is marched 1->N while water is marched N->1.
    N = int(g.rows)
    counter = water_thermal_arrangement.startswith("Counter")
    tw_in_rows = np.full(N, float(water_in_C))
    final_rows = None
    converged = False
    iterations = 0
    for it in range(80):
        iterations = it + 1
        air_state = dict(ain)
        rows_out = []
        tw_out_rows = np.zeros(N)
        for i in range(N):
            seg = _row_segment(g, geom, air_state, float(tw_in_rows[i]), mdot_da,
                               coolant_kind, glycol_pct, water_pressure_Pa, hyd, h_air,
                               air_fouling_m2K_W, water_fouling_m2K_W)
            tw_out_rows[i] = seg["water_out_C"]
            rows_out.append((dict(air_state), float(tw_in_rows[i]), seg))
            air_state = dict(seg["air_out"])
            air_state["pressure_Pa"] = air_in_cond.pressure_Pa

        new_tw = np.array(tw_in_rows, dtype=float)
        if counter:
            new_tw[N - 1] = water_in_C
            for i in range(N - 2, -1, -1):
                new_tw[i] = tw_out_rows[i + 1]
        else:
            new_tw[0] = water_in_C
            for i in range(1, N):
                new_tw[i] = tw_out_rows[i - 1]
        err = float(np.max(np.abs(new_tw - tw_in_rows)))
        tw_in_rows = 0.25 * tw_in_rows + 0.75 * new_tw
        final_rows = rows_out
        if err < 2.0e-4:
            converged = True
            # One final evaluation on converged row inlet temperatures.
            air_state = dict(ain)
            final_rows = []
            for i in range(N):
                seg = _row_segment(g, geom, air_state, float(tw_in_rows[i]), mdot_da,
                                   coolant_kind, glycol_pct, water_pressure_Pa, hyd, h_air,
                                   air_fouling_m2K_W, water_fouling_m2K_W)
                final_rows.append((dict(air_state), float(tw_in_rows[i]), seg))
                air_state = dict(seg["air_out"])
                air_state["pressure_Pa"] = air_in_cond.pressure_Pa
            break

    row_records = []
    Q_total = Q_sensible = Q_latent = 0.0
    for i, (ai, twi, seg) in enumerate(final_rows, start=1):
        ao = seg["air_out"]
        Q_total += seg["Q_W"]
        Q_sensible += seg["Q_sensible_W"]
        Q_latent += seg["Q_latent_W"]
        row_records.append({
            "Row_air_sequence": i,
            "Air_in_DB_C": ai["T_C"],
            "Air_out_DB_C": ao["T_C"],
            "Air_in_WB_C": ai["Twb_C"],
            "Air_out_WB_C": ao["Twb_C"],
            "Air_in_RH_pct": ai["RH_pct"],
            "Air_out_RH_pct": ao["RH_pct"],
            "Air_in_W_g_kgda": ai["W"] * 1000.0,
            "Air_out_W_g_kgda": ao["W"] * 1000.0,
            "Water_in_C": twi,
            "Water_out_C": seg["water_out_C"],
            "Q_total_kW": seg["Q_W"] / 1000.0,
            "Q_sensible_kW": seg["Q_sensible_W"] / 1000.0,
            "Q_latent_kW": seg["Q_latent_W"] / 1000.0,
            "Wet_fraction_pct": seg["wet_fraction"] * 100.0,
            "Surface_mode": seg["surface_mode"],
            "C_air_kW_K": seg["C_air_W_K"] / 1000.0,
            "C_water_kW_K": seg["C_water_W_K"] / 1000.0,
            "Cr": seg["Cr"],
            "NTU_dry": seg["NTU_dry"],
            "Effectiveness_dry": seg["effectiveness_dry"],
            "Re_water": seg["Re_water"],
            "Pr_water": seg["Pr_water"],
        })
    row_df = pd.DataFrame(row_records)

    aout = final_rows[-1][2]["air_out"]
    water_out_C = final_rows[0][2]["water_out_C"] if counter else final_rows[-1][2]["water_out_C"]
    mean_w = 0.5 * (water_in_C + water_out_C)
    props_final = coolant_props(coolant_kind, glycol_pct, mean_w, water_pressure_Pa)
    water_ht_final = water_side_htc(geom, hyd.circuits, hyd.water_mass_flow_kg_s, props_final, hyd.tube_roughness_m)
    hydres = water_pressure_drop(geom, hyd, props_final, water_ht_final)

    wet_frac_Q = 0.0
    if Q_total > 1e-9:
        wet_frac_Q = sum(r[2]["wet_fraction"] * r[2]["Q_W"] for r in final_rows) / Q_total
    dry_fraction = 1.0 - wet_frac_Q
    dp_air = aircorr["dp_air_dry_Pa"] * (dry_fraction + wet_frac_Q * wet_air_dp_factor)
    condensate = mdot_da * max(ain["W"] - aout["W"], 0.0)
    SHR = min(max(Q_sensible / max(Q_total, 1e-12), 0.0), 1.0)

    C_air = mdot_da * ain["cp_da"]
    C_w = hyd.water_mass_flow_kg_s * props_final["cp"]
    Cmin, Cmax = min(C_air, C_w), max(C_air, C_w)
    Cr = Cmin / max(Cmax, 1e-12)
    UA_dry_sum = float(sum(r[2]["UA_dry_W_K"] for r in final_rows))
    NTU_ref = UA_dry_sum / max(Cmin, 1e-12)
    eps_ref = crossflow_effectiveness(NTU_ref, Cr, Cmin_is_water=(C_w <= C_air))
    hsat_wi = saturation_enthalpy(water_in_C, air_in_cond.pressure_Pa)
    eps_h = (ain["h_J_kgda"] - aout["h_J_kgda"]) / max(ain["h_J_kgda"] - hsat_wi, 1e-12)
    eps_T_air = (ain["T_C"] - aout["T_C"]) / max(ain["T_C"] - water_in_C, 1e-12)

    # Resistance diagnostics on full-coil area at mean coolant properties.
    eta_fd = fin_efficiency_staggered(g, h_air, 1.0)
    eta_od = overall_surface_efficiency(geom, eta_fd)
    R_air = 1.0 / max(eta_od * h_air * geom["A_air_total_m2"], 1e-12) + air_fouling_m2K_W / max(geom["A_air_total_m2"], 1e-12)
    R_water_film = 1.0 / max(water_ht_final["h_water_W_m2K"] * geom["A_i_total_m2"], 1e-12) + water_fouling_m2K_W / max(geom["A_i_total_m2"], 1e-12)
    R_wall = math.log(g.tube_od_m / geom["Di_m"]) / (2.0 * math.pi * g.tube_k_W_mK * geom["L_total_tube_m"])
    R_total = R_air + R_water_film + R_wall
    resistance_limiting = "Air side" if R_air >= (R_water_film + R_wall) else "Water/tube side"
    capacity_rate_limiting = "Air side" if C_air <= C_w else "Water/coolant side"

    # Air velocity reporting: face velocity and velocity through minimum free area between fins/tubes.
    face_velocity = air_volume_flow_m3_s / max(geom["face_area_m2"], 1e-12)
    u_core = aircorr["u_max_m_s"]

    # Surface mode summary.
    modes = [r[2]["surface_mode"] for r in final_rows]
    if all(m == "Dry" for m in modes):
        surf_mode = "Dry"
    elif all(m == "Fully wet" for m in modes):
        surf_mode = "Fully wet"
    else:
        surf_mode = "Mixed / partially wet by row"

    return {
        "geometry": geom,
        "air_in": ain,
        "air_out": aout,
        "air_corr": aircorr,
        "water_props": props_final,
        "water_ht": water_ht_final,
        "hydraulics": hydres,
        "row_table": row_df,
        "row_marching_converged": converged,
        "row_marching_iterations": iterations,
        "water_thermal_arrangement": water_thermal_arrangement,
        "Q_total_kW": Q_total / 1000.0,
        "Q_sensible_kW": Q_sensible / 1000.0,
        "Q_latent_kW": max(Q_total - Q_sensible, 0.0) / 1000.0,
        "SHR": SHR,
        "water_out_C": water_out_C,
        "condensate_kg_h": condensate * 3600.0,
        "f_dry": dry_fraction,
        "wet_fraction": wet_frac_Q,
        "surface_mode": surf_mode,
        "air_dp_Pa": dp_air,
        "eta_fin_dry": eta_fd,
        "eta_o_dry": eta_od,
        "UA_dry_W_K": UA_dry_sum,
        "mdot_da_kg_s": mdot_da,
        "face_velocity_m_s": face_velocity,
        "core_max_velocity_m_s": u_core,
        "C_air_kW_K": C_air / 1000.0,
        "C_water_kW_K": C_w / 1000.0,
        "Cmin_kW_K": Cmin / 1000.0,
        "Cmax_kW_K": Cmax / 1000.0,
        "capacity_ratio_Cr": Cr,
        "NTU_dry_reference": NTU_ref,
        "effectiveness_dry_reference": eps_ref,
        "effectiveness_enthalpy_wet": eps_h,
        "effectiveness_air_temperature": eps_T_air,
        "capacity_rate_limiting_side": capacity_rate_limiting,
        "resistance_limiting_side": resistance_limiting,
        "R_air_fraction": R_air / max(R_total, 1e-12),
        "R_water_fraction": R_water_film / max(R_total, 1e-12),
        "R_wall_fraction": R_wall / max(R_total, 1e-12),
    }


def design_recommendations(
    base_result: Dict[str, object],
    target: Dict[str, object],
    g: CoilGeometry,
    air_in_cond: AirCondition,
    air_volume_flow_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_in_C: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    air_htc_multiplier: float = 1.0,
    air_dp_multiplier: float = 1.0,
    wet_air_dp_factor: float = 1.12,
    air_fouling_m2K_W: float = 0.0,
    water_fouling_m2K_W: float = 0.0,
    water_thermal_arrangement: str = "Counterflow / water enters air-leaving side",
) -> pd.DataFrame:
    """One-variable-at-a-time design alternatives when the selected coil misses the target.

    "Best" is ranked by the smallest relative input change, not lifecycle cost. The table shows
    resulting air/water pressure drops so the engineer can reject an option with excessive penalty.
    """
    if target_is_met(base_result, target):
        return pd.DataFrame([{"Option": "Current design", "Change": "No change required", "Target_met": True,
                              "Q_kW": base_result["Q_total_kW"], "Air_out_DB_C": base_result["air_out"]["T_C"],
                              "Air_out_WB_C": base_result["air_out"]["Twb_C"], "Air_dP_Pa": base_result["air_dp_Pa"],
                              "Water_dP_kPa": base_result["hydraulics"]["dp_total_avg_kPa"], "Relative_change_pct": 0.0}])

    candidates = []
    common = dict(air_htc_multiplier=air_htc_multiplier, air_dp_multiplier=air_dp_multiplier,
                  wet_air_dp_factor=wet_air_dp_factor, air_fouling_m2K_W=air_fouling_m2K_W,
                  water_fouling_m2K_W=water_fouling_m2K_W, water_thermal_arrangement=water_thermal_arrangement)

    # 1) Add rows, up to 16 total.
    for nr in range(g.rows + 1, min(16, g.rows + 6) + 1):
        gg = CoilGeometry(g.face_width_m, g.face_height_m, nr, g.transverse_pitch_m, g.longitudinal_pitch_m,
                          g.tube_od_m, g.tube_thickness_m, g.fpi, g.fin_thickness_m, g.fin_k_W_mK,
                          g.tube_k_W_mK, g.wave_amplitude_2x_m, g.wave_half_period_m)
        rr = thermal_performance(gg, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
                                 water_in_C, water_pressure_Pa, hyd, **common)
        if target_is_met(rr, target):
            candidates.append((100.0 * (nr / g.rows - 1.0), "Increase rows", f"{g.rows} → {nr} rows", rr))
            break

    # 2) Increase water flow. This can help when water-side capacity rate/HTC is restrictive.
    for mult in [1.15, 1.30, 1.50, 1.75, 2.00]:
        hh = HydraulicInputs(hyd.circuits, hyd.water_mass_flow_kg_s * float(mult), hyd.inlet_header_od_m,
                             hyd.inlet_header_thickness_m, hyd.outlet_header_od_m, hyd.outlet_header_thickness_m,
                             hyd.header_length_m, hyd.header_arrangement, hyd.tube_roughness_m, hyd.header_roughness_m,
                             hyd.return_bend_K, hyd.branch_takeoff_K, hyd.common_entry_K, hyd.common_exit_K)
        rr = thermal_performance(g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
                                 water_in_C, water_pressure_Pa, hh, **common)
        if target_is_met(rr, target):
            candidates.append((100.0 * (float(mult) - 1.0), "Increase water flow",
                               f"{hyd.water_mass_flow_kg_s:.3f} → {hh.water_mass_flow_kg_s:.3f} kg/s", rr))
            break

    # 3) Increase face area while preserving aspect ratio. Air volume flow is held constant.
    for area_mult in [1.15, 1.30, 1.50, 1.75, 2.00]:
        linear = math.sqrt(float(area_mult))
        gg = CoilGeometry(g.face_width_m * linear, g.face_height_m * linear, g.rows,
                          g.transverse_pitch_m, g.longitudinal_pitch_m, g.tube_od_m, g.tube_thickness_m,
                          g.fpi, g.fin_thickness_m, g.fin_k_W_mK, g.tube_k_W_mK,
                          g.wave_amplitude_2x_m, g.wave_half_period_m)
        # Header length follows face height if the original header length approximately did.
        hdrL = hyd.header_length_m * linear if abs(hyd.header_length_m - g.face_height_m) < 0.15 * g.face_height_m else hyd.header_length_m
        hh = HydraulicInputs(hyd.circuits, hyd.water_mass_flow_kg_s, hyd.inlet_header_od_m,
                             hyd.inlet_header_thickness_m, hyd.outlet_header_od_m, hyd.outlet_header_thickness_m,
                             hdrL, hyd.header_arrangement, hyd.tube_roughness_m, hyd.header_roughness_m,
                             hyd.return_bend_K, hyd.branch_takeoff_K, hyd.common_entry_K, hyd.common_exit_K)
        rr = thermal_performance(gg, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct,
                                 water_in_C, water_pressure_Pa, hh, **common)
        if target_is_met(rr, target):
            candidates.append((100.0 * (float(area_mult) - 1.0), "Increase coil face area",
                               f"{g.face_width_m:.2f}×{g.face_height_m:.2f} → {gg.face_width_m:.2f}×{gg.face_height_m:.2f} m", rr))
            break

    rows = []
    for rel, option, change, rr in sorted(candidates, key=lambda x: x[0]):
        rows.append({
            "Option": option,
            "Change": change,
            "Relative_change_pct": rel,
            "Target_met": True,
            "Q_kW": rr["Q_total_kW"],
            "Air_out_DB_C": rr["air_out"]["T_C"],
            "Air_out_WB_C": rr["air_out"]["Twb_C"],
            "Air_out_RH_pct": rr["air_out"]["RH_pct"],
            "Air_dP_Pa": rr["air_dp_Pa"],
            "Water_dP_kPa": rr["hydraulics"]["dp_total_avg_kPa"],
            "Tube_velocity_m_s": rr["water_ht"]["velocity_m_s"],
        })
    if not rows:
        rows.append({
            "Option": "Combination required",
            "Change": "No single change within +6 rows, +100% water flow, or +100% face area met the target.",
            "Relative_change_pct": np.nan,
            "Target_met": False,
            "Q_kW": base_result["Q_total_kW"],
            "Air_out_DB_C": base_result["air_out"]["T_C"],
            "Air_out_WB_C": base_result["air_out"]["Twb_C"],
            "Air_out_RH_pct": base_result["air_out"]["RH_pct"],
            "Air_dP_Pa": base_result["air_dp_Pa"],
            "Water_dP_kPa": base_result["hydraulics"]["dp_total_avg_kPa"],
            "Tube_velocity_m_s": base_result["water_ht"]["velocity_m_s"],
        })
    return pd.DataFrame(rows)


# Extend warnings for v2 diagnostics while preserving the earlier checks.
_warnings_for_result_v1 = warnings_for_result

def warnings_for_result(result: Dict[str, object]) -> List[str]:
    w = _warnings_for_result_v1(result)
    if not result.get("row_marching_converged", True):
        w.append("Row-by-row counterflow iteration did not fully converge; review the case before using the result.")
    if result.get("capacity_ratio_Cr", 0.0) > 0.95:
        w.append("Air and coolant heat-capacity rates are very similar (Cr > 0.95); performance is sensitive to UA and flow arrangement.")
    if result.get("R_air_fraction", 0.0) > 0.70:
        w.append("More than 70% of the calculated dry thermal resistance is on the air side; increasing water flow alone is unlikely to give a large capacity gain.")
    if result.get("R_water_fraction", 0.0) > 0.50:
        w.append("Water-side film resistance is a major part of total resistance; review tube velocity, number of circuits, coolant viscosity and water flow.")
    return w
