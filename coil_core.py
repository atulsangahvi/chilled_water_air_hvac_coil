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

from dataclasses import dataclass, asdict, replace
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
    fin_type: str = "Wavy + louvers"


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


def air_state_from_db_wb(db_C: float, wb_C: float, P: float = P_ATM) -> Dict[str, float]:
    """Humid-air state from dry-bulb and thermodynamic wet-bulb temperature."""
    _need_coolprop()
    db_C = float(db_C)
    wb_C = min(float(wb_C), db_C)
    T = db_C + 273.15
    B = wb_C + 273.15
    W = HAPropsSI("W", "T", T, "P", P, "B", B)
    h = HAPropsSI("H", "T", T, "P", P, "W", W)
    RH = HAPropsSI("R", "T", T, "P", P, "W", W) * 100.0
    Tdp = HAPropsSI("D", "T", T, "P", P, "W", W) - 273.15
    try:
        Vda = HAPropsSI("Vda", "T", T, "P", P, "W", W)
    except Exception:
        Vda = 287.055 * T * (1.0 + 1.6078 * W) / P
    rho_ha = (1.0 + W) / max(Vda, 1e-12)
    cp_da = 1006.0 + W * 1860.0
    return dict(T_C=db_C, RH_pct=RH, W=W, h_J_kgda=h, Tdp_C=Tdp, Twb_C=wb_C,
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

    # Waviness changes developed fin area. Plain fin has no area-length enhancement.
    if g.fin_type == "Plain fin":
        sec_theta = 1.0
    else:
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
    bank_rows: int | None = None,
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
        * max(int(bank_rows if bank_rows is not None else g.rows), 1) ** (-0.069)
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


def airside_wang_plain(
    geom: Dict[str, float], g: CoilGeometry, air_in: Dict[str, float], Vdot_m3_s: float,
    air_htc_multiplier: float = 1.0, air_dp_multiplier: float = 1.0,
    bank_rows: int | None = None,
) -> Dict[str, float]:
    """Wang, Chi & Chang (2000) plain-fin j/f correlation.

    This is the preferred path for continuous *plain* plate fins.  The expression is the
    published correlation also used in the user's heat-pipe project.  It remains an
    empirical correlation and should be calibrated against the actual fin die/collar.
    """
    rho = air_in["rho_ha"]
    mdot_ha = rho * Vdot_m3_s
    A_c = geom["free_flow_area_m2"]
    Gmax = mdot_ha / max(A_c, 1e-12)
    u_max = Gmax / max(rho, 1e-12)
    mu, k = dry_air_transport(air_in["T_C"], P_ATM)
    cp_ha = (1006.0 + air_in["W"] * 1860.0) / max(1.0 + air_in["W"], 1e-12)
    Pr = cp_ha * mu / max(k, 1e-12)
    Re = Gmax * g.tube_od_m / max(mu, 1e-12)
    N = max(int(bank_rows if bank_rows is not None else g.rows), 1)

    # Hydraulic diameter based on free volume / wetted air-side surface.
    Dh = 4.0 * A_c * geom["depth_m"] / max(geom["A_air_total_m2"], 1e-12)
    Dh = max(Dh, 1e-6)
    Fp = geom["fin_pitch_m"]
    Dc = g.tube_od_m
    Pt = g.transverse_pitch_m
    Pl = g.longitudinal_pitch_m
    Re_eff = max(Re, 120.0)
    lnRe = math.log(Re_eff)
    FpDc = Fp / Dc
    FpDh = Fp / Dh
    FpPt = Fp / Pt
    PtPl = Pt / Pl

    P3 = -0.361 - 0.042 * N / lnRe + 0.158 * math.log(max(N * (FpDc ** 0.41), 1e-12))
    P4 = -1.224 - 0.076 * (Pl / Dh) ** 1.42 / lnRe
    P5 = -0.083 + 0.058 * N / lnRe
    P6 = -5.735 + 1.21 * math.log(max(Re_eff / N, 1e-12))
    j = 0.086 * Re_eff ** P3 * N ** P4 * FpDc ** P5 * FpDh ** P6 * FpPt ** (-0.93)

    F1 = -0.764 + 0.739 * PtPl + 0.177 * FpDc - 0.00758 / N
    F2 = -15.689 + 64.021 / lnRe
    F3 = 1.696 - 15.695 / lnRe
    f = 0.0267 * Re_eff ** F1 * PtPl ** F2 * FpDc ** F3
    j = max(float(j), 1e-5)
    f = max(float(f), 1e-5)

    h_a = j * Gmax * cp_ha / max(Pr ** (2.0 / 3.0), 1e-12)
    dp = f * (geom["A_air_total_m2"] / max(A_c, 1e-12)) * Gmax ** 2 / (2.0 * max(rho, 1e-12))
    return {
        "h_air_W_m2K": h_a * air_htc_multiplier,
        "dp_air_dry_Pa": dp * air_dp_multiplier,
        "j": j, "f_air": f, "Re_air": Re, "Pr_air": Pr,
        "u_max_m_s": u_max, "mdot_ha_kg_s": mdot_ha,
        "correlation": "Wang-Chi-Chang 2000 plain fin",
        "correlation_note": "Published plain-fin j/f correlation",
    }


def airside_dispatch(
    geom: Dict[str, float], g: CoilGeometry, air_in: Dict[str, float], Vdot_m3_s: float,
    air_htc_multiplier: float = 1.0, air_dp_multiplier: float = 1.0,
    bank_rows: int | None = None,
) -> Dict[str, float]:
    """Select an air-side model that matches the selected fin family.

    * Plain fin: Wang, Chi & Chang (2000).
    * Wavy + louvers: Wang-Tsai-Lu correlation as documented by ACHP.
    * Wavy fin: transparent engineering baseline using the plain-fin Wang correlation on
      the developed wavy area.  The 1999 Wang-Jang-Chiou paper confirms a dedicated wavy
      correlation exists, but its full equation is not reproduced in the open references
      bundled with this project; therefore no invented coefficients are used here.
    """
    if g.fin_type == "Plain fin":
        return airside_wang_plain(geom, g, air_in, Vdot_m3_s, air_htc_multiplier, air_dp_multiplier, bank_rows)
    if g.fin_type == "Wavy fin":
        out = airside_wang_plain(geom, g, air_in, Vdot_m3_s, air_htc_multiplier, air_dp_multiplier, bank_rows)
        out["correlation"] = "Wavy fin - Wang plain-fin baseline on developed wavy area"
        out["correlation_note"] = "Calibration required; dedicated Wang-Jang-Chiou 1999 coefficients not hard-coded without a verified equation source"
        return out
    out = airside_wang_wavy_louvered(geom, g, air_in, Vdot_m3_s, air_htc_multiplier, air_dp_multiplier, bank_rows)
    out["correlation"] = "Wang-Tsai-Lu wavy+louvered fin"
    out["correlation_note"] = "As documented by ACHP"
    return out


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
    air_bank_rows: int | None = None,
    compute_hydraulics: bool = True,
) -> Dict[str, object]:
    geom = geometry_areas(g)
    ain = air_state_from_db_rh(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
    mdot_da = air_volume_flow_m3_s / ain["Vda_m3_kgda"]
    aircorr = airside_dispatch(geom, g, ain, air_volume_flow_m3_s,
                               air_htc_multiplier, air_dp_multiplier,
                               bank_rows=(air_bank_rows if air_bank_rows is not None else g.rows))

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
    hydres = water_pressure_drop(geom, hyd, props_final, water_ht_final) if compute_hydraulics else {}

    return {
        "geometry": geom,
        "fin_type": g.fin_type,
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
        "R_air_dry_K_W": R_outside_dry,
        "R_water_plus_wall_K_W": R_inside,
        "R_wall_K_W": R_wall,
        "R_air_fouling_K_W": R_fo,
        "R_water_fouling_K_W": R_fi,
        "mdot_da_kg_s": mdot_da,
    }


def segmented_thermal_performance(
    g: CoilGeometry,
    air_in_cond: AirCondition,
    air_volume_flow_m3_s: float,
    coolant_kind: str,
    glycol_pct: float,
    water_in_C: float,
    water_pressure_Pa: float,
    hyd: HydraulicInputs,
    water_row_progression: str = "Water enters air-leaving side (cross-counterflow tendency)",
    air_htc_multiplier: float = 1.0,
    air_dp_multiplier: float = 1.0,
    wet_air_dp_factor: float = 1.12,
    air_fouling_m2K_W: float = 0.0,
    water_fouling_m2K_W: float = 0.0,
    max_iter: int = 60,
    tol_K: float = 2e-4,
) -> Dict[str, object]:
    """Equivalent row-bank chilled-water march with *physical cross-flow* at every row.

    Air always crosses the tube axes approximately at 90 degrees.  The user option only
    specifies which side of the coil depth receives the cold-water connection:

    - water enters the air-leaving side -> cross-counterflow *row progression*;
    - water enters the air-entering side -> cross-parallelflow *row progression*.

    The local row heat exchanger is therefore never modeled as a physical parallel-flow
    exchanger.  Dry-row effectiveness uses the cross-flow relation in ``thermal_performance``.
    The reverse row progression requires an iterative water-temperature profile because air
    and water boundary conditions are known at opposite ends of the bank.
    """
    if hyd.circuits > max(int(math.floor(g.face_height_m / g.transverse_pitch_m)), 1):
        # More circuits than tubes in a row is not physically routeable for the common
        # one-tube-per-circuit-per-row serpentine pattern.
        pass

    full_ref = thermal_performance(
        g, air_in_cond, air_volume_flow_m3_s, coolant_kind, glycol_pct, water_in_C,
        water_pressure_Pa, hyd, air_htc_multiplier, air_dp_multiplier, wet_air_dp_factor,
        air_fouling_m2K_W, water_fouling_m2K_W,
    )
    mdot_da_fixed = float(full_ref["mdot_da_kg_s"])
    geom_full = full_ref["geometry"]
    nrows = int(g.rows)
    row_g = replace(g, rows=1)
    reverse = water_row_progression.startswith("Water enters air-leaving")

    def run_profile(boundaries: list[float] | None = None):
        air_cond = AirCondition(air_in_cond.db_C, air_in_cond.rh_pct, air_in_cond.pressure_Pa)
        logs = []
        air_dp_sum = 0.0
        q_sum = qs_sum = ql_sum = 0.0
        if reverse:
            assert boundaries is not None
            predicted = list(boundaries)
            predicted[-1] = water_in_C
        else:
            predicted = [water_in_C] + [water_in_C] * nrows
            tw_current = water_in_C

        last_rr = None
        for i in range(nrows):
            if reverse:
                tw_row_in = float(boundaries[i + 1])
            else:
                tw_row_in = float(tw_current)

            _row_ain = air_state_from_db_rh(air_cond.db_C, air_cond.rh_pct, air_cond.pressure_Pa)
            row_Vdot = mdot_da_fixed * _row_ain["Vda_m3_kgda"]
            rr = thermal_performance(
                row_g, air_cond, row_Vdot, coolant_kind, glycol_pct,
                tw_row_in, water_pressure_Pa, hyd, air_htc_multiplier,
                air_dp_multiplier, wet_air_dp_factor, air_fouling_m2K_W,
                water_fouling_m2K_W, air_bank_rows=nrows,
            )
            last_rr = rr
            tw_row_out = float(rr["water_out_C"])
            if reverse:
                predicted[i] = tw_row_out
            else:
                predicted[i + 1] = tw_row_out
                tw_current = tw_row_out

            ain_r = rr["air_in"]
            aout_r = rr["air_out"]
            C_air_r = rr["mdot_da_kg_s"] * ain_r["cp_da"]
            C_w_r = hyd.water_mass_flow_kg_s * rr["water_props"]["cp"]
            Cmin_r, Cmax_r = min(C_air_r, C_w_r), max(C_air_r, C_w_r)
            Cr_r = Cmin_r / max(Cmax_r, 1e-12)
            NTU_r = rr["UA_dry_W_K"] / max(Cmin_r, 1e-12)
            eps_r = crossflow_effectiveness(NTU_r, Cr_r, Cmin_is_water=(C_w_r <= C_air_r))
            logs.append({
                "Row_air_sequence": i + 1,
                "Air_in_DB_C": ain_r["T_C"],
                "Air_out_DB_C": aout_r["T_C"],
                "Air_in_WB_C": ain_r["Twb_C"],
                "Air_out_WB_C": aout_r["Twb_C"],
                "Air_in_RH_pct": ain_r["RH_pct"],
                "Air_out_RH_pct": aout_r["RH_pct"],
                "Air_in_W_g_kgda": 1000.0 * ain_r["W"],
                "Air_out_W_g_kgda": 1000.0 * aout_r["W"],
                "Water_in_C": tw_row_in,
                "Water_out_C": tw_row_out,
                "Q_total_kW": rr["Q_total_kW"],
                "Q_sensible_kW": rr["Q_sensible_kW"],
                "Q_latent_kW": rr["Q_latent_kW"],
                "Wet_fraction_pct": 100.0 * rr["wet_fraction"],
                "Surface_mode": rr["surface_mode"],
                "C_air_kW_K": C_air_r / 1000.0,
                "C_water_kW_K": C_w_r / 1000.0,
                "Cr": Cr_r,
                "NTU_dry": NTU_r,
                "Effectiveness_dry_crossflow": eps_r,
                "Re_air": rr["air_corr"]["Re_air"],
                "Pr_air": rr["air_corr"]["Pr_air"],
                "Re_water": rr["water_ht"]["Re_water"],
                "Pr_water": rr["water_ht"]["Pr_water"],
            })
            q_sum += rr["Q_total_kW"]
            qs_sum += rr["Q_sensible_kW"]
            ql_sum += rr["Q_latent_kW"]
            air_dp_sum += rr["air_dp_Pa"]
            air_cond = AirCondition(aout_r["T_C"], aout_r["RH_pct"], air_in_cond.pressure_Pa)
        return predicted, logs, last_rr, q_sum, qs_sum, ql_sum, air_dp_sum

    converged = True
    iterations = 1
    if reverse:
        # Seed from the full-bank energy balance: water is coldest at the air-leaving side
        # and warmest at the air-entering side.
        tw_out_seed = max(water_in_C, float(full_ref["water_out_C"]))
        boundaries = list(np.linspace(tw_out_seed, water_in_C, nrows + 1))
        converged = False
        for it in range(1, max_iter + 1):
            predicted, logs, last_rr, q_sum, qs_sum, ql_sum, air_dp_sum = run_profile(boundaries)
            delta = max(abs(predicted[j] - boundaries[j]) for j in range(nrows))
            # Under-relax to stabilize wet/dry row-state changes near dew point.
            boundaries = [0.55 * boundaries[j] + 0.45 * predicted[j] for j in range(nrows)] + [water_in_C]
            iterations = it
            if delta < tol_K:
                converged = True
                break
        # One final pass using the converged profile for the reported rows.
        predicted, logs, last_rr, q_sum, qs_sum, ql_sum, air_dp_sum = run_profile(boundaries)
        water_out_C = float(predicted[0])
    else:
        predicted, logs, last_rr, q_sum, qs_sum, ql_sum, air_dp_sum = run_profile(None)
        water_out_C = float(predicted[-1])

    if last_rr is None:
        raise RuntimeError("Row marching returned no rows.")
    aout = last_rr["air_out"]
    ain = full_ref["air_in"]
    mdot_da = full_ref["mdot_da_kg_s"]
    mean_w = 0.5 * (water_in_C + water_out_C)
    props_final = coolant_props(coolant_kind, glycol_pct, mean_w, water_pressure_Pa)
    water_ht_final = water_side_htc(geom_full, hyd.circuits, hyd.water_mass_flow_kg_s, props_final, hyd.tube_roughness_m)
    hydres = water_pressure_drop(geom_full, hyd, props_final, water_ht_final)

    wet_fr = float(np.mean([r["Wet_fraction_pct"] for r in logs]) / 100.0)
    modes = {r["Surface_mode"] for r in logs}
    if modes == {"Dry"}:
        surface_state = "Dry by row"
    elif modes == {"Fully wet"}:
        surface_state = "Fully wet by row"
    else:
        surface_state = "Mixed / partially wet by row"

    C_air = mdot_da * ain["cp_da"]
    C_w = hyd.water_mass_flow_kg_s * props_final["cp"]
    Cmin, Cmax = min(C_air, C_w), max(C_air, C_w)
    Cr = Cmin / max(Cmax, 1e-12)
    NTU = full_ref["UA_dry_W_K"] / max(Cmin, 1e-12)
    eps_dry = crossflow_effectiveness(NTU, Cr, Cmin_is_water=(C_w <= C_air))
    eps_T = (ain["T_C"] - aout["T_C"]) / max(ain["T_C"] - water_in_C, 1e-12)
    hsat_wi = saturation_enthalpy(water_in_C, air_in_cond.pressure_Pa)
    eps_h = (ain["h_J_kgda"] - aout["h_J_kgda"]) / max(ain["h_J_kgda"] - hsat_wi, 1e-12)

    Rair = max(full_ref.get("R_air_dry_K_W", 0.0), 0.0)
    Rwall = max(full_ref.get("R_wall_K_W", 0.0), 0.0)
    Rinside = max(full_ref.get("R_water_plus_wall_K_W", 0.0), 0.0)
    Rwater = max(Rinside - Rwall, 0.0)
    Rtot = max(Rair + Rwater + Rwall, 1e-12)
    resistance_pct = {
        "air": 100.0 * Rair / Rtot,
        "water": 100.0 * Rwater / Rtot,
        "wall": 100.0 * Rwall / Rtot,
    }
    capacity_limiting = "Coolant side" if C_w < C_air else "Air side"
    resistance_limiting = max(resistance_pct, key=resistance_pct.get).capitalize() + " side"

    condensate = mdot_da * max(ain["W"] - aout["W"], 0.0) * 3600.0
    SHR = min(max(qs_sum / max(q_sum, 1e-12), 0.0), 1.0)
    face_velocity = air_volume_flow_m3_s / max(geom_full["face_area_m2"], 1e-12)

    return {
        "geometry": geom_full,
        "fin_type": g.fin_type,
        "air_in": ain,
        "air_out": aout,
        "air_corr": full_ref["air_corr"],
        "water_props": props_final,
        "water_ht": water_ht_final,
        "hydraulics": hydres,
        "Q_total_kW": q_sum,
        "Q_sensible_kW": qs_sum,
        "Q_latent_kW": max(ql_sum, 0.0),
        "SHR": SHR,
        "water_out_C": water_out_C,
        "condensate_kg_h": condensate,
        "wet_fraction": wet_fr,
        "surface_mode": surface_state,
        "air_dp_Pa": air_dp_sum,
        "UA_dry_W_K": full_ref["UA_dry_W_K"],
        "mdot_da_kg_s": mdot_da,
        "row_table": pd.DataFrame(logs),
        "row_march_converged": converged,
        "row_march_iterations": iterations,
        "physical_flow_geometry": "Cross-flow: air is perpendicular to tube/coolant flow",
        "water_row_progression": water_row_progression,
        "face_velocity_m_s": face_velocity,
        "max_air_velocity_m_s": full_ref["air_corr"]["u_max_m_s"],
        "C_air_kW_K": C_air / 1000.0,
        "C_coolant_kW_K": C_w / 1000.0,
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
    }


def target_load_db_wb(air_in: AirCondition, air_out_db_C: float, air_out_wb_C: float, Vdot_m3_s: float) -> Dict[str, float]:
    ain = air_state_from_db_rh(air_in.db_C, air_in.rh_pct, air_in.pressure_Pa)
    aout = air_state_from_db_wb(air_out_db_C, air_out_wb_C, air_in.pressure_Pa)
    mdot_da = Vdot_m3_s / ain["Vda_m3_kgda"]
    Q = mdot_da * (ain["h_J_kgda"] - aout["h_J_kgda"])
    Qs = mdot_da * ain["cp_da"] * (air_in.db_C - air_out_db_C)
    return {"Q_required_kW": Q / 1000.0, "Q_sensible_required_kW": Qs / 1000.0,
            "SHR_required": Qs / max(Q, 1e-12), "mdot_da_kg_s": mdot_da,
            "target_air": aout}


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
    if result.get("fin_type") == "Plain fin":
        if ac["Re_air"] < 300 or ac["Re_air"] > 20000:
            w.append("Air Reynolds number is outside the approximate 300-20000 range used for the Wang plain-fin correlation; extrapolation is occurring.")
    elif ac["Re_air"] < 300 or ac["Re_air"] > 8000:
        w.append("Air Reynolds number is outside the approximate range used for the current wavy/louvered air-side model; extrapolation is occurring.")
    if result.get("fin_type") == "Wavy fin":
        w.append("Wavy-fin mode currently uses a transparent plain-fin Wang baseline on developed wavy area. Calibrate h and dP against the actual wavy fin die before production use.")
    if wh["Re_water"] < 3000:
        w.append("Water-side Reynolds number is below 3000; turbulent Gnielinski performance is not fully established and heat transfer may be transition/laminar.")
    if wh["velocity_m_s"] < 0.45:
        w.append("Low tube water velocity (<0.45 m/s): check fouling risk, air removal and low Reynolds number.")
    if wh["velocity_m_s"] > 2.4:
        w.append("High tube water velocity (>2.4 m/s): check erosion, noise and return-bend/header losses for your tube material and water quality.")
    if hyd["header_supply_velocity_m_s"] > 2.5 or hyd["header_return_velocity_m_s"] > 2.5:
        w.append("Header velocity exceeds 2.5 m/s; consider a larger header ID and check noise/erosion criteria.")
    if hyd["header_path_spread_kPa"] > max(0.15 * hyd["dp_total_avg_kPa"], 2.0):
        if hyd.get("model", "").startswith("Explicit routed-circuit"):
            w.append("Large residual circuit-path pressure spread remains in the explicit routed-circuit network; review circuit pass balance, bend spans, branch positions and header sizing.")
        else:
            w.append("Large calculated circuit-path pressure spread: equal-flow assumption may be poor. Define the physical circuit map in the Circuiting tab or revise headers/balancing.")
    if hyd.get("flow_imbalance_pct_max", 0.0) > 10.0:
        w.append(f"Explicit circuit-flow maldistribution is high ({hyd['flow_imbalance_pct_max']:.1f}% maximum deviation from equal flow). Review circuit lengths and header branch locations.")
    if result["air_dp_Pa"] > 300:
        w.append("Air-side coil pressure drop is high (>300 Pa); check face velocity, FPI, rows and wet correction against fan static allowance.")
    return w
