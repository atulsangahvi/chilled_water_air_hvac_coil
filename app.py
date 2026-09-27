from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Chilled Water Cooling Coil Designer v2.5.4",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import require_login, logout
from coil_core import (
    AirCondition,
    CoilGeometry,
    HydraulicInputs,
    MM,
    air_state_from_db_wb,
    coolant_props,
    suggested_coolant_flow_for_velocity,
    segmented_thermal_performance,
    target_load,
    target_load_db_wb,
    warnings_for_result,
)
from reporting import build_pdf, build_output_pdf
from target_assessment import assess_chilled_water_target
from hydraulic_margin import coolant_pressure_margin
from tube2d import coupled_tube_by_tube_performance
from circuiting import (
    auto_serpentine_routes,
    compatibility_summary,
    parse_route_text,
    route_text,
    route_geometry_table,
    tube_id,
    validate_routes,
)
from circuit_picker import apply_tube_click
from circuit_plot import selected_tube, tube_plot

CFM_TO_M3S = 0.00047194745
MATERIAL_K = {
    "Aluminum": 205.0,
    "Copper": 380.0,
    "Steel": 50.0,
    "CuNi 90/10": 50.0,
    "CuNi 70/30": 29.0,
    "Brass 63/37": 120.0,
    "Brass 80/20": 160.0,
}

require_login()

IS_ADMIN = str(st.session_state.get("role", "engineer")).strip().lower() == "admin"

with st.sidebar:
    st.success(f"Logged in: {st.session_state.username} ({st.session_state.role})")
    if st.button("Logout", use_container_width=True):
        logout()
    st.divider()
    st.caption("Engineering model v2.5.4 - fully coupled tube-by-tube thermal + physical circuiting")
    st.caption("Air crosses the tube axes; water connection side only changes row progression.")

st.title("💧 Chilled Water Cooling Coil Designer v2.5.4")
st.caption(
    "Wet/dry cooling - row-by-row air and coolant temperatures - air/water dP - "
    "target checking - multi-user Streamlit"
)

input_tab, circuit_tab, result_tab, method_tab = st.tabs(["📐 Design Inputs", "🔀 Circuiting", "📊 Results", "📚 Method & Validation"])

with input_tab:
    with st.expander("Report / project details", expanded=False):
        r1, r2 = st.columns(2)
        customer_name = r1.text_input("Customer name", value="")
        contact_name = r2.text_input("Contact", value="")
        r1, r2 = st.columns(2)
        project_name = r1.text_input("Project", value="")
        reference = r2.text_input("Reference", value="")
        r1, r2 = st.columns(2)
        tag_description = r1.text_input("Tag / description", value="")
        coil_quantity = r2.number_input("Coil quantity", min_value=1, max_value=999, value=1, step=1)

    st.subheader("1. Coil face and tube bank")
    c1, c2, c3 = st.columns(3)
    face_W = c1.number_input("Coil face width / tube length (m)", 0.20, 6.0, 1.20, 0.01)
    face_H = c2.number_input("Coil face height (m)", 0.20, 4.0, 0.85, 0.01)
    rows = c3.number_input(
        "Number of tube rows in coil (airflow direction)",
        min_value=1, max_value=20, value=6, step=1,
        help=(
            "Physical number of tube rows through the coil depth, counted in the air-flow direction. "
            "Examples: 4-row, 6-row, 8-row cooling coil. This directly changes heat-transfer area, "
            "coil depth, air pressure drop and the row-by-row water/air temperature marching."
        ),
    )

    c1, c2, c3 = st.columns(3)
    Pt_mm = c1.number_input("Vertical tube pitch (mm)", 12.0, 60.0, 25.4, 0.1,
                            help="Vertical/transverse pitch between tubes in one row.")
    Pl_mm = c2.number_input("Row pitch, airflow depth (mm)", 10.0, 60.0, 22.0, 0.1,
                            help="Distance between successive tube rows through coil depth.")
    tube_shape = st.selectbox("Tube cross-section", ["Round tube", "Flat / elliptical tube"])
    Do_mm = c3.number_input("Tube OD (mm)" if tube_shape=="Round tube" else "Flat tube outside major axis, airflow direction (mm)",
                             5.0, 50.0 if tube_shape!="Round tube" else 25.0, 9.53, 0.01)
    if tube_shape=="Round tube":
        minor_mm=Do_mm
        fin_construction="Plate fins"
    else:
        d1,d2=st.columns(2)
        minor_mm=d1.number_input("Flat tube outside minor axis, vertical (mm)",2.0,30.0,6.0,0.1)
        fin_construction=d2.selectbox("Fin construction",["Plate fins","Serpentine fins"])
        st.caption("Ellipse major axis lies in airflow depth; its minor axis is vertical. "
                   "Serpentine fin FPI counts individual inclined legs along tube length.")
        if minor_mm>=Do_mm:
            st.error("Flat tube minor axis must be smaller than its airflow-aligned major axis.")
            st.stop()
    st.info(
        f"Selected coil: **{int(rows)} rows** through airflow depth | "
        f"Nominal tube-bank depth = **{int(rows) * Pl_mm:.1f} mm** "
        f"({int(rows)} x {Pl_mm:.1f} mm row pitch)."
    )

    st.subheader("2. Fin and tube construction")
    c1, c2, c3 = st.columns(3)
    FPI = c1.number_input(
        "Serpentine fin FPI (individual legs per inch)" if fin_construction=="Serpentine fins" else "Plate fin FPI (plates per inch)",
        4.0, 30.0, 10.0, 0.5,
        help="Direct manufacturing input. Fin pitch = 25.4/FPI mm.",
    )
    tf_mm = c2.number_input("Fin thickness (mm)", 0.05, 0.50, 0.12, 0.01)
    fin_type = c3.selectbox("Fin type", ["Plain fin", "Wavy fin", "Wavy + louvers"], index=2) if fin_construction=="Plate fins" else "Wavy fin"
    st.caption(f"Calculated fin pitch = {25.4/FPI:.3f} mm ({FPI:.1f} FPI)")
    st.caption('Geometry: tube length = coil face width; tubes per row count up coil face height; '
               'row pitch adds airflow depth. '+('Each plate spans face height × total depth and repeats along tube length.'
               if fin_construction=='Plate fins' else
               'Corrugated strips fill vertical tube gaps; FPI counts inclined legs along tube length.'))

    c1, c2, c3 = st.columns(3)
    fin_mat = c1.selectbox("Fin material", ["Aluminum", "Copper", "Steel", "Brass 63/37"], index=0)
    tube_mat = c2.selectbox("Tube material", ["Copper", "Aluminum", "Steel", "Brass 63/37", "Brass 80/20", "CuNi 90/10", "CuNi 70/30"], index=0)
    tw_mm = c3.number_input("Tube wall thickness (mm)", 0.20, 3.0, 0.35, 0.01)

    with st.expander("Fin geometry, fouling and correlation calibration"):
        if fin_type == "Plain fin":
            wave_2a_mm = 0.0
            wave_half_mm = 1.0
            st.info("Plain-fin geometry selected: no wave amplitude or louver geometry is applied.")
        else:
            a1, a2 = st.columns(2)
            wave_2a_mm = a1.number_input("Wave parameter Pd / twice amplitude (mm)", 0.05, 5.0, 1.0, 0.05)
            wave_half_mm = a2.number_input("Wave half-period xf (mm)", 0.10, 10.0, 1.0, 0.05)
            if fin_type == "Wavy fin":
                st.warning(
                    "Wavy-only mode uses the verified Wang plain-fin j/f baseline with the corrected physical fin/tube area. "
                    "Do not treat it as a final manufacturer correlation until the exact wavy-fin die is calibrated."
                )
            else:
                st.caption("Wavy + louvers uses the Wang-Tsai-Lu correlation documented by ACHP.")
        a1, a2, a3 = st.columns(3)
        h_mult = a1.number_input("Air HTC calibration multiplier", 0.50, 2.00, 1.00, 0.01)
        dp_mult = a2.number_input("Dry air dP calibration multiplier", 0.50, 3.00, 1.00, 0.01)
        wet_dp_factor = a3.number_input(
            "Wet/dry air dP ratio on wetted area", 0.70, 3.00, 1.12, 0.01,
            help="Calibrate from wet-coil pressure-drop test or trusted selection data.",
        )
        b1, b2 = st.columns(2)
        Rfo = b1.number_input("Air-side fouling resistance (m2.K/W)", 0.0, 0.003, 0.0, 0.00005, format="%.5f")
        Rfi = b2.number_input("Water-side fouling resistance (m2.K/W)", 0.0, 0.003, 0.0, 0.00005, format="%.5f")

    st.subheader("3. Entering air and airflow")
    c1, c2, c3 = st.columns(3)
    air_state_mode = c1.selectbox("Entering air condition", ["DB + RH %", "DB + WB"], index=0)
    Tair_in = c2.number_input("Entering air DB (degC)", -5.0, 65.0, 27.0, 0.1)
    if air_state_mode == "DB + RH %":
        RHair_in = c3.number_input("Entering air RH (%)", 1.0, 100.0, 50.0, 0.5)
        WBair_in = None
    else:
        WBair_in = c3.number_input("Entering air WB (degC)", -10.0, float(Tair_in), min(19.0, float(Tair_in)), 0.1)
        try:
            _ain_ui = air_state_from_db_wb(Tair_in, WBair_in, 101325.0)
            RHair_in = float(_ain_ui["RH_pct"])
            st.caption(f"Calculated entering RH = {RHair_in:.1f}%")
        except Exception:
            RHair_in = 50.0

    c1, c2, c3 = st.columns(3)
    airflow_mode = c1.selectbox(
        "Airflow input unit",
        ["Face velocity (m/s)", "Volume flow (m3/s)", "Volume flow (m3/h)", "Volume flow (CFM)"],
        index=0,
    )
    if airflow_mode == "Face velocity (m/s)":
        air_value = c2.number_input("Face velocity (m/s)", 0.20, 8.0, 2.0, 0.05)
        Vdot = air_value * face_W * face_H
    elif airflow_mode == "Volume flow (m3/s)":
        air_value = c2.number_input("Air volume (m3/s)", 0.05, 100.0, 2.04, 0.01)
        Vdot = air_value
    elif airflow_mode == "Volume flow (m3/h)":
        air_value = c2.number_input("Air volume (m3/h)", 100.0, 500000.0, 7344.0, 50.0)
        Vdot = air_value / 3600.0
    else:
        air_value = c2.number_input("Air volume (CFM)", 100.0, 500000.0, 4323.0, 25.0)
        Vdot = air_value * CFM_TO_M3S
    P_air_kPa = c3.number_input("Air pressure (kPa abs)", 60.0, 130.0, 101.325, 0.1)
    if air_state_mode == "DB + WB":
        try:
            RHair_in = float(air_state_from_db_wb(Tair_in, WBair_in, P_air_kPa * 1000.0)["RH_pct"])
        except Exception:
            pass
    face_v = Vdot / max(face_W * face_H, 1e-12)
    st.info(
        f"Equivalent airflow: **{Vdot:.4f} m3/s** = **{Vdot*3600:.0f} m3/h** = "
        f"**{Vdot/CFM_TO_M3S:.0f} CFM** | gross face velocity **{face_v:.3f} m/s**"
    )

    st.subheader("4. Design target")
    target_mode = st.radio(
        "Design/check mode",
        ["Required leaving air condition", "Required cooling capacity (kW)"],
        horizontal=True,
    )
    target_kW = None
    target_state_mode = None
    target_T = target_RH = target_WB = None
    if target_mode == "Required cooling capacity (kW)":
        target_kW = st.number_input("Required total cooling (kW)", 0.1, 5000.0, 50.0, 0.5)
    else:
        c1, c2, c3 = st.columns(3)
        target_state_mode = c1.selectbox("Leaving air target format", ["DB + RH %", "DB + WB"], index=0)
        target_T = c2.number_input("Target leaving DB (degC)", -5.0, 40.0, 13.0, 0.1)
        if target_state_mode == "DB + RH %":
            target_RH = c3.number_input("Target leaving RH (%)", 10.0, 100.0, 90.0, 0.5)
        else:
            target_WB = c3.number_input("Target leaving WB (degC)", -10.0, float(target_T), min(12.0, float(target_T)), 0.1)

    st.subheader("5. Chilled water / glycol")
    c1, c2, c3 = st.columns(3)
    coolant = c1.selectbox("Coolant", ["Water", "Ethylene Glycol", "Propylene Glycol"], index=0)
    glycol = c2.number_input(
        "Glycol concentration (% mass)", 0.0, 60.0,
        0.0 if coolant == "Water" else 25.0, 1.0,
        disabled=(coolant == "Water"),
    )
    Tw_in = c3.number_input("Entering coolant temperature (degC)", -15.0, 30.0, 7.0, 0.1)

    # Circuit count is intentionally entered before flow so the app can propose a starting flow
    # corresponding to a user-selected tube velocity (default 1.0 m/s).
    c1, c2, c3 = st.columns(3)
    water_pressure_kPa = c1.number_input("Coolant pressure (kPa abs)", 80.0, 2500.0, 300.0, 10.0)
    circuits = c2.number_input("Parallel water circuits", 1, 300, 12, 1)
    suggested_tube_velocity = c3.number_input(
        "Starting tube velocity for flow suggestion (m/s)", 0.20, 3.00, 1.00, 0.05,
        help="Starting-point only. The suggested total flow is based on tube ID x parallel circuits x this velocity. You remain free to enter any flow you want.",
    )

    try:
        rho_ui = coolant_props(coolant, glycol, Tw_in, water_pressure_kPa * 1000.0)["rho"]
    except Exception:
        rho_ui = 1000.0
    suggestion = suggested_coolant_flow_for_velocity(
        Do_mm * MM, tw_mm * MM, int(circuits), suggested_tube_velocity, rho_ui,
        minor_mm * MM
    )
    tube_ID_m_ui = suggestion["tube_ID_m"]
    tube_flow_area_ui = suggestion["tube_flow_area_m2"]
    suggested_Vdot_m3_s = suggestion["volume_flow_m3_s"]
    suggested_Vdot_m3_h = suggestion["volume_flow_m3_h"]
    suggested_L_s = suggestion["volume_flow_L_s"]
    suggested_mdot_kg_s = suggestion["mass_flow_kg_s"]

    st.info(
        f"**Suggested starting coolant flow for {suggested_tube_velocity:.2f} m/s in each tube:** "
        f"**{suggested_Vdot_m3_h:.3f} m3/h** = **{suggested_L_s:.3f} L/s** = "
        f"**{suggested_mdot_kg_s:.3f} kg/s** at the current coolant density. "
        f"Basis: internal major/minor axes **{suggestion['tube_inner_major_axis_m']*1000:.3f}/"
        f"{suggestion['tube_inner_minor_axis_m']*1000:.3f} mm** (hydraulic diameter "
        f"**{tube_ID_m_ui*1000:.3f} mm**), {int(circuits)} parallel circuits. "
        "This is only a starting recommendation; edit the actual flow below as required."
    )

    water_input = st.selectbox(
        "Coolant flow input",
        ["Volume flow (m3/h)", "Volume flow (L/s)", "Mass flow (kg/s)"],
        index=0,
        help="Choose the unit you normally receive from the project data. The app converts it internally and all calculations use the entered flow.",
    )
    if water_input == "Mass flow (kg/s)":
        default_mass = float(st.session_state.get("cw_mass_flow_input", suggested_mdot_kg_s))
        mdot_w = st.number_input("Total coolant mass flow (kg/s)", 0.02, 200.0, default_mass, 0.01, key="cw_mass_flow_input")
        Vw_m3h = mdot_w / max(rho_ui, 1e-12) * 3600.0
        Vw_L_s = Vw_m3h / 3.6
    elif water_input == "Volume flow (L/s)":
        default_L_s = float(st.session_state.get("cw_volume_flow_L_s_input", suggested_L_s))
        Vw_L_s = st.number_input("Total coolant volume flow (L/s)", 0.01, 300.0, default_L_s, 0.01, key="cw_volume_flow_L_s_input")
        Vw_m3h = Vw_L_s * 3.6
        mdot_w = (Vw_L_s / 1000.0) * rho_ui
    else:
        default_vol = float(st.session_state.get("cw_volume_flow_input", suggested_Vdot_m3_h))
        Vw_m3h = st.number_input("Total coolant volume flow (m3/h)", 0.05, 1000.0, default_vol, 0.05, key="cw_volume_flow_input")
        Vw_L_s = Vw_m3h / 3.6
        mdot_w = (Vw_m3h / 3600.0) * rho_ui

    actual_tube_velocity_ui = (mdot_w / max(rho_ui, 1e-12)) / max(int(circuits) * tube_flow_area_ui, 1e-12)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entered coolant flow", f"{Vw_m3h:.3f} m3/h")
    c2.metric("Equivalent flow", f"{Vw_L_s:.3f} L/s")
    c3.metric("Mass flow / circuit", f"{mdot_w/int(circuits):.4f} kg/s")
    c4.metric("Approx tube velocity", f"{actual_tube_velocity_ui:.3f} m/s")

    circuit_connection_style = st.selectbox(
        "Circuit supply/return tube-end arrangement",
        [
            "Same tube end (even passes/circuit required)",
            "Opposite tube ends (odd passes/circuit required)",
        ],
        index=0,
        help=(
            "This is a manufacturing/circuiting constraint. If both supply and return headers connect at the same tube end, "
            "each complete circuit normally needs an even number of straight tube passes. If inlet and outlet are on opposite tube ends, an odd pass count exits at the opposite end."
        ),
    )
    tubes_per_row_ui = max(int(math.floor(face_H / max(Pt_mm * MM, 1e-12))), 1)
    total_tubes_ui = tubes_per_row_ui * int(rows)
    comp_ui = compatibility_summary(total_tubes_ui, int(circuits), circuit_connection_style)
    c1, c2, c3 = st.columns(3)
    c1.metric("Calculated tubes / row", f"{tubes_per_row_ui}")
    c2.metric("Total tubes", f"{total_tubes_ui}", help="Tubes/row x number of rows")
    if comp_ui.get("fully_balanced_compatible"):
        pass_text = str(comp_ui["passes_per_circuit"])
    elif comp_ui.get("fully_compatible"):
        pass_text = f"{comp_ui['pass_count_min']}-{comp_ui['pass_count_max']} unequal"
    else:
        pass_text = "special circuit needed"
    c3.metric("Tube passes / circuit", pass_text)
    if comp_ui.get("fully_balanced_compatible"):
        st.success(
            f"Balanced circuiting: {total_tubes_ui} tubes / {int(circuits)} circuits = "
            f"{comp_ui['passes_per_circuit']} passes per circuit with the correct outlet-end parity."
        )
    elif comp_ui.get("fully_compatible"):
        counts_txt = ", ".join(str(x) for x in comp_ui.get("recommended_pass_counts", []))
        st.info(
            "Unequal circuit lengths are physically allowed for this geometry. The closest all-tubes-used, "
            f"header-end-compatible pass pattern is: **{counts_txt}**. The explicit hydraulic and 2-D thermal "
            "solvers will calculate the resulting flow and temperature maldistribution rather than rejecting it."
        )
    else:
        nearby = ", ".join(str(x) for x in comp_ui.get("nearby_routeable_circuit_counts", [])) or "none in current search range"
        st.warning(
            "This circuit count cannot use every tube while keeping every circuit on the selected return-header end. "
            f"Nearby all-tubes routeable circuit counts: {nearby}. A special crossover/Z circuit or deliberately dropped tube(s) would be required."
        )

    st.subheader("6. Physical flow geometry and water connection")
    st.info(
        "**Physical coil geometry is CROSS-FLOW.** Air travels through coil depth and crosses the tube axes; "
        "coolant travels along the tubes, so the local air/coolant velocity directions are approximately 90 degrees."
    )
    water_progression = st.selectbox(
        "Water connection / row progression across coil depth",
        [
            "Water enters air-leaving side (cross-counterflow tendency)",
            "Water enters air-entering side (cross-parallelflow tendency)",
        ],
        index=0,
        help=(
            "This does NOT change the local cross-flow geometry. It only tells the row-marching model whether "
            "the coldest water meets the last air row or the first air row."
        ),
    )

    st.subheader("7. Headers and circuit pressure loss")
    c1, c2, c3 = st.columns(3)
    hdr_in_od_mm = c1.number_input("Supply header OD (mm)", 10.0, 300.0, 42.4, 0.1)
    hdr_in_t_mm = c2.number_input("Supply header wall thickness (mm)", 0.5, 15.0, 1.5, 0.1)
    hdr_out_od_mm = c3.number_input("Return header OD (mm)", 10.0, 300.0, 42.4, 0.1)
    c1, c2, c3 = st.columns(3)
    hdr_out_t_mm = c1.number_input("Return header wall thickness (mm)", 0.5, 15.0, 1.5, 0.1)
    hdr_L = c2.number_input("Header length (m)", 0.10, 10.0, float(face_H), 0.05)
    hdr_arr = c3.selectbox("Header outlet arrangement", ["Opposite-end (reverse-return tendency)", "Same-end"], index=0)
    header_feed_end = st.selectbox(
        "Supply header feed end", ["Top", "Bottom"], index=0,
        help="Used by the explicit circuit network to calculate the header friction path to each circuit branch."
    )

    with st.expander("Hydraulic fitting coefficients"):
        c1, c2, c3 = st.columns(3)
        bend_K = c1.number_input("Return bend K per bend", 0.0, 10.0, 1.5, 0.1)
        branch_K = c2.number_input("Circuit takeoff/return K", 0.0, 10.0, 0.5, 0.1)
        entry_K = c3.number_input("Common inlet K", 0.0, 10.0, 0.5, 0.1)
        c1, c2, c3 = st.columns(3)
        exit_K = c1.number_input("Common outlet K", 0.0, 10.0, 1.0, 0.1)
        tube_rough_um = c2.number_input("Tube absolute roughness (um)", 0.1, 100.0, 1.5, 0.1)
        hdr_rough_um = c3.number_input("Header absolute roughness (um)", 0.1, 100.0, 1.5, 0.1)

    geom_obj = CoilGeometry(
        face_width_m=face_W,
        face_height_m=face_H,
        rows=int(rows),
        transverse_pitch_m=Pt_mm * MM,
        longitudinal_pitch_m=Pl_mm * MM,
        tube_od_m=Do_mm * MM,
        tube_minor_axis_m=minor_mm * MM if tube_shape!="Round tube" else None,
        fin_construction=fin_construction,
        tube_thickness_m=tw_mm * MM,
        fpi=FPI,
        fin_thickness_m=tf_mm * MM,
        fin_k_W_mK=MATERIAL_K[fin_mat],
        tube_k_W_mK=MATERIAL_K[tube_mat],
        wave_amplitude_2x_m=wave_2a_mm * MM,
        wave_half_period_m=wave_half_mm * MM,
        fin_type=fin_type,
    )
    air_obj = AirCondition(Tair_in, RHair_in, P_air_kPa * 1000.0)
    hyd_obj = HydraulicInputs(
        int(circuits), mdot_w,
        hdr_in_od_mm * MM, hdr_in_t_mm * MM,
        hdr_out_od_mm * MM, hdr_out_t_mm * MM,
        hdr_L, hdr_arr,
        tube_rough_um * 1e-6, hdr_rough_um * 1e-6,
        bend_K, branch_K, entry_K, exit_K,
    )

    run_from_circuit_tab = st.session_state.pop("run_from_circuit_tab", False)
    if st.button("🚀 Run chilled-water coil analysis", type="primary", use_container_width=True) or run_from_circuit_tab:
        try:
            routes_now = {int(k): list(v) for k, v in st.session_state.get("circuit_routes", {}).items()}
            route_signature_now = json.dumps({str(k): v for k, v in sorted(routes_now.items())}, sort_keys=True)
            route_check = validate_routes(
                routes_now, int(rows), tubes_per_row_ui, int(circuits), circuit_connection_style,
                Pt_mm * MM, Pl_mm * MM,
            ) if routes_now else None

            if route_check and route_check["complete"] and route_check["valid"]:
                with st.spinner("Solving fully coupled tube-by-tube air/coolant grid..."):
                    res = coupled_tube_by_tube_performance(
                        geom_obj, air_obj, Vdot, coolant, glycol, Tw_in, water_pressure_kPa * 1000.0,
                        hyd_obj, routes_now, header_feed_end, water_progression,
                        h_mult, dp_mult, wet_dp_factor, Rfo, Rfi,
                    )
                res["circuit_routes"] = routes_now
                res["circuit_validation"] = route_check
                res["circuit_model"] = "Explicit routed circuits + fully coupled 2-D tube-by-tube thermal model"
                res["circuit_route_signature"] = route_signature_now
            else:
                res = segmented_thermal_performance(
                    geom_obj, air_obj, Vdot, coolant, glycol, Tw_in, water_pressure_kPa * 1000.0,
                    hyd_obj, water_progression, h_mult, dp_mult, wet_dp_factor, Rfo, Rfi,
                )
                res["circuit_routes"] = routes_now
                res["circuit_validation"] = route_check
                if routes_now and route_check:
                    reason = "circuit map incomplete or invalid"
                    if route_check.get("errors"):
                        reason += ": " + " | ".join(route_check.get("errors", [])[:3])
                    res["circuit_model"] = f"Equivalent row-bank model ({reason})"
                else:
                    res["circuit_model"] = "Equivalent row-bank model (complete physical circuit route not yet defined)"
                res["circuit_route_signature"] = route_signature_now
            if target_mode == "Required cooling capacity (kW)":
                tar = {
                    "target_mode": target_mode,
                    "Q_required_kW": float(target_kW),
                    "target_air": None,
                    "target_format": None,
                }
            elif target_state_mode == "DB + WB":
                tar = target_load_db_wb(air_obj, target_T, target_WB, Vdot)
                tar.update({"target_mode": target_mode, "target_format": target_state_mode})
            else:
                tar = target_load(air_obj, target_T, target_RH, Vdot)
                tar["target_air"] = {
                    "T_C": target_T,
                    "RH_pct": target_RH,
                }
                # Include humidity ratio so target checking is based on moisture content, not RH alone.
                try:
                    from coil_core import air_state_from_db_rh
                    tar["target_air"] = air_state_from_db_rh(target_T, target_RH, air_obj.pressure_Pa)
                except Exception:
                    pass
                tar.update({"target_mode": target_mode, "target_format": target_state_mode})

            res["pressure_margin"] = coolant_pressure_margin(
                water_pressure_kPa, res["hydraulics"]["dp_total_max_kPa"],
                Tw_in, res["water_out_C"], coolant,
            )
            res["target_assessment"] = assess_chilled_water_target(res, tar)
            target_met = res["target_assessment"]["thermal_target_met"]

            inp = {
                "version": "2.5.4",
                "customer_name": customer_name, "contact_name": contact_name,
                "project_name": project_name, "reference": reference,
                "tag_description": tag_description, "coil_quantity": int(coil_quantity),
                "face_width_m": face_W, "face_height_m": face_H, "rows": int(rows),
                "Pt_mm": Pt_mm, "Pl_mm": Pl_mm, "tube_OD_mm": Do_mm, "tube_wall_mm": tw_mm,
                "tube_shape":tube_shape,"tube_minor_axis_mm":minor_mm,
                "fin_construction":fin_construction,
                "FPI": FPI, "fin_pitch_mm": 25.4/FPI, "fin_thickness_mm": tf_mm,
                "fin_type": fin_type, "fin_material": fin_mat, "tube_material": tube_mat,
                "wave_2a_mm": wave_2a_mm, "wave_half_mm": wave_half_mm,
                "air_state_mode": air_state_mode, "air_in_DB_C": Tair_in,
                "air_in_RH_pct": RHair_in, "air_in_WB_C": WBair_in,
                "airflow_input_mode": airflow_mode, "airflow_input_value": air_value,
                "airflow_m3_s": Vdot, "airflow_m3_h": Vdot*3600.0,
                "airflow_CFM": Vdot/CFM_TO_M3S, "face_velocity_m_s": face_v,
                "air_pressure_kPa_abs": P_air_kPa,
                "coolant": coolant, "glycol_pct": glycol, "water_in_C": Tw_in,
                "water_pressure_kPa_abs": water_pressure_kPa, "water_mdot_kg_s": mdot_w,
                "water_volume_m3_h": Vw_m3h, "circuits": int(circuits),
                "suggested_tube_velocity_m_s": suggested_tube_velocity,
                "suggested_water_volume_m3_h": suggested_Vdot_m3_h,
                "suggested_water_L_s": suggested_L_s,
                "suggested_water_mdot_kg_s": suggested_mdot_kg_s,
                "actual_input_tube_velocity_m_s": actual_tube_velocity_ui,
                "calculated_tubes_per_row": tubes_per_row_ui, "calculated_total_tubes": total_tubes_ui,
                "circuit_connection_style": circuit_connection_style, "header_feed_end": header_feed_end,
                "physical_flow_geometry": "Cross-flow (air perpendicular to tube/coolant direction)",
                "water_row_progression": water_progression,
                "header_supply_OD_mm": hdr_in_od_mm, "header_supply_t_mm": hdr_in_t_mm,
                "header_return_OD_mm": hdr_out_od_mm, "header_return_t_mm": hdr_out_t_mm,
                "header_length_m": hdr_L, "header_arrangement": hdr_arr,
                "air_htc_multiplier": h_mult, "air_dp_multiplier": dp_mult,
                "wet_air_dp_factor": wet_dp_factor,
                "air_fouling_m2K_W": Rfo, "water_fouling_m2K_W": Rfi,
                "return_bend_K": bend_K, "branch_takeoff_K": branch_K,
                "common_entry_K": entry_K, "common_exit_K": exit_K,
                "tube_roughness_um": tube_rough_um, "header_roughness_um": hdr_rough_um,
                "target_mode": target_mode, "target_format": target_state_mode,
                "target_kW": target_kW, "target_DB_C": target_T,
                "target_RH_pct": target_RH, "target_WB_C": target_WB,
            }
            st.session_state["cw_result"] = res
            st.session_state["cw_target"] = tar
            st.session_state["cw_target_met"] = target_met
            st.session_state["cw_inputs"] = inp
            st.success("Analysis complete. Open the Results tab.")
        except Exception as exc:
            st.exception(exc)


with circuit_tab:
    st.subheader("Interactive physical water-circuit editor")
    st.markdown(
        "Each dot represents one straight tube pass. **R1** is the entering-air row and the last row is the leaving-air row. "
        "**T1** is the top tube position. Select a circuit, then click tube positions in the exact order that water travels. "
        "Each jump between two selected tubes represents a return bend at the end of the coil."
    )

    geometry_signature = (int(rows), int(tubes_per_row_ui), int(circuits), circuit_connection_style)
    if st.session_state.get("circuit_geometry_signature") != geometry_signature:
        st.session_state["circuit_geometry_signature"] = geometry_signature
        st.session_state["circuit_routes"] = {i: [] for i in range(1, int(circuits) + 1)}
        st.session_state["circuit_editor_message"] = (
            "Circuit map reset because row count, tubes/row, number of circuits, or tube-end arrangement changed."
        )
    routes = st.session_state.setdefault("circuit_routes", {i: [] for i in range(1, int(circuits) + 1)})
    for i in range(1, int(circuits) + 1):
        routes.setdefault(i, [])
    for k in list(routes):
        if int(k) > int(circuits):
            routes.pop(k, None)

    if st.session_state.get("circuit_editor_message"):
        st.info(st.session_state.pop("circuit_editor_message"))

    comp = compatibility_summary(total_tubes_ui, int(circuits), circuit_connection_style)
    a, b, c, d = st.columns(4)
    a.metric("Rows", int(rows))
    b.metric("Tubes / row", tubes_per_row_ui)
    c.metric("Total tubes", total_tubes_ui)
    d.metric("Selected circuits", int(circuits))
    if comp.get("fully_balanced_compatible"):
        st.success(f"Balanced circuiting basis: {comp['passes_per_circuit']} tube passes per circuit.")
    elif comp.get("fully_compatible"):
        st.info(
            "Unequal but header-end-compatible circuiting is allowed. Suggested pass counts: "
            + ", ".join(map(str, comp.get("recommended_pass_counts", [])))
            + ". The longer/shorter routes will be solved individually for flow, dP and heat transfer."
        )
    else:
        nearby = ", ".join(map(str, comp.get("nearby_routeable_circuit_counts", []))) or "none"
        st.warning(
            "All tubes cannot be assigned to this many circuits with the selected common outlet end. "
            f"Nearby routeable circuit counts: {nearby}; otherwise use a special crossover/Z circuit or dropped tubes."
        )

    v = validate_routes(routes, int(rows), tubes_per_row_ui, int(circuits), circuit_connection_style, Pt_mm*MM, Pl_mm*MM)
    a, b, c = st.columns(3)
    a.metric("Assigned tubes", f"{v['assigned_count']} / {v['total_tubes']}")
    b.metric("Route status", "Complete" if v["complete"] else "Incomplete")
    c.metric("Pass balance", "Balanced" if v["balanced"] else "Unequal / incomplete")
    for err in v["errors"]:
        st.error(err)
    for warn in v["warnings"][:6]:
        st.warning(warn)

    selected_circuit = st.selectbox("Active circuit", list(range(1, int(circuits)+1)), key="active_circuit")
    st.markdown("#### Click tubes in the circuit drawing")
    st.caption("Choose an active circuit, then click each circle in water-flow order. Click the last selected circle again to undo that pass. Coloured paths update as you work.")
    drawing = tube_plot(int(rows), tubes_per_row_ui, routes)
    drawing_signature = json.dumps({"geometry": geometry_signature, "routes": routes,
                                    "active": selected_circuit}, sort_keys=True)
    drawing_key = ("cw_tube_plot_" + hashlib.sha256(drawing_signature.encode()).hexdigest()[:16]
                   + f"_{st.session_state.get('cw_plot_nonce', 0)}")

    def on_circuit_drawing_click():
        point = selected_tube(st.session_state[drawing_key].selection,
                              len(drawing.data) - 1)
        if point:
            try:
                st.session_state["circuit_editor_message"] = apply_tube_click(
                    st.session_state["circuit_routes"],
                    st.session_state.get("active_circuit", 1), point,
                    int(rows), tubes_per_row_ui,
                )
            except ValueError as exc:
                st.session_state["circuit_editor_message"] = str(exc)
            # A new chart key clears the prior selection, so the same tube can
            # be clicked again to undo even when the route did not change.
            st.session_state["cw_plot_nonce"] = st.session_state.get("cw_plot_nonce", 0) + 1

    st.plotly_chart(drawing, use_container_width=True, key=drawing_key,
                    on_select=on_circuit_drawing_click, selection_mode="points",
                    config={"displaylogo": False, "displayModeBar": False})
    st.caption(
        "The coloured lines are the ordered circuit route through the row/tube matrix. The drawing is a circuit cross-section; "
        "actual straight tubes run along the face width, perpendicular to this view."
    )

    st.markdown("#### Build or edit a circuit")
    selected_route = routes[selected_circuit]
    st.code(route_text(selected_route) if selected_route else "No tubes assigned yet", language="text")

    x1, x2, x3, x4 = st.columns(4)
    if x1.button("Undo last tube", use_container_width=True, disabled=not bool(selected_route)):
        routes[selected_circuit] = selected_route[:-1]
        st.session_state["circuit_editor_message"] = f"Removed last tube from Circuit {selected_circuit}."
        st.rerun()
    if x2.button("Clear active circuit", use_container_width=True, disabled=not bool(selected_route)):
        routes[selected_circuit] = []
        st.session_state["circuit_editor_message"] = f"Cleared Circuit {selected_circuit}."
        st.rerun()
    if x3.button("Clear all circuits", use_container_width=True):
        st.session_state["circuit_routes"] = {i: [] for i in range(1, int(circuits)+1)}
        st.session_state["circuit_editor_message"] = "Cleared all circuit routes."
        st.rerun()
    if x4.button("Auto-generate serpentine", type="primary", use_container_width=True, disabled=not comp["fully_compatible"]):
        try:
            st.session_state["circuit_routes"] = auto_serpentine_routes(
                int(rows), tubes_per_row_ui, int(circuits), circuit_connection_style, water_progression
            )
            st.session_state["circuit_editor_message"] = "Generated nearest-neighbour serpentine circuits using the parity-compatible pass distribution and the selected water row progression. Unequal circuit lengths are retained when required by geometry; review bend pattern and calculated maldistribution before manufacturing."
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    with st.expander("Numerical route entry (alternative to clicking dots)"):
        manual = st.text_area(
            f"Circuit {selected_circuit} route",
            value=route_text(routes[selected_circuit]),
            key=f"manual_route_{selected_circuit}_{geometry_signature}",
            help="Example: R6-T1 -> R5-T1 -> R4-T2 -> R3-T2",
        )
        if st.button("Apply numerical route", key=f"apply_route_{selected_circuit}"):
            try:
                proposed = parse_route_text(manual)
                old_route = list(routes[selected_circuit])
                routes[selected_circuit] = proposed
                check = validate_routes(routes, int(rows), tubes_per_row_ui, int(circuits), circuit_connection_style, Pt_mm*MM, Pl_mm*MM)
                if check["errors"]:
                    routes[selected_circuit] = old_route
                    st.error("Route not applied: " + " | ".join(check["errors"]))
                else:
                    st.session_state["circuit_editor_message"] = f"Applied numerical route to Circuit {selected_circuit}."
                    st.rerun()
            except Exception as exc:
                st.error(str(exc))

    if int(rows) <= 12:
        st.markdown("#### Click-to-route tube matrix (alternative editor)")
        st.caption(
            "Click an unassigned dot to append it to the active circuit. A dot already in the active circuit can be removed by clicking it only when it is the last tube in that circuit; this protects route order."
        )
        vv = validate_routes(routes, int(rows), tubes_per_row_ui, int(circuits), circuit_connection_style)
        owner = vv["owner"]
        # Header row
        hc = st.columns([0.55] + [1.0] * int(rows))
        hc[0].markdown("**Tube**")
        for rr in range(1, int(rows)+1):
            hc[rr].markdown(f"**R{rr}**")
        for tt in range(1, tubes_per_row_ui + 1):
            cols = st.columns([0.55] + [1.0] * int(rows))
            cols[0].markdown(f"**T{tt}**")
            for rr in range(1, int(rows)+1):
                label = tube_id(rr, tt)
                own = owner.get(label)
                if own:
                    seq = routes[own].index(label) + 1
                    txt = f"C{own}"
                    help_txt = f"{label}: Circuit {own}, sequence {seq}"
                else:
                    txt = "○"
                    help_txt = f"{label}: unassigned"
                if cols[rr].button(
                    txt, key=f"tube_btn_{rr}_{tt}_{geometry_signature}",
                    help=help_txt, use_container_width=True,
                    type="primary" if own == selected_circuit else "secondary",
                ):
                    if own is None:
                        routes[selected_circuit].append(label)
                        st.session_state["circuit_editor_message"] = f"Added {label} to Circuit {selected_circuit}."
                        st.rerun()
                    elif own == selected_circuit and routes[selected_circuit] and routes[selected_circuit][-1] == label:
                        routes[selected_circuit].pop()
                        st.session_state["circuit_editor_message"] = f"Removed {label} from Circuit {selected_circuit}."
                        st.rerun()
                    elif own == selected_circuit:
                        st.warning(f"{label} is not the last tube in Circuit {selected_circuit}. Use Undo last tube to preserve sequence.")
                    else:
                        st.warning(f"{label} is already assigned to Circuit {own}.")
    else:
        st.info("For more than 12 tube rows, use Auto-generate or Numerical route entry to avoid an excessively narrow click grid.")

    st.markdown("#### Circuit route summary")
    summary_rows = []
    for cc in range(1, int(circuits)+1):
        route = routes.get(cc, [])
        summary_rows.append({
            "Circuit": cc, "Passes": len(route),
            "Inlet tube": route[0] if route else "-", "Outlet tube": route[-1] if route else "-",
            "Outlet tube end": ("Same as supply" if len(route) and len(route)%2==0 else "Opposite to supply") if route else "-",
            "Route": route_text(route),
        })
    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, use_container_width=True, hide_index=True, height=320)
    if st.button("🚀 Run analysis using these circuits",type="primary",
                 disabled=not (v["valid"] and v["complete"]),
                 help="A complete, valid route uses every selected tube in the coupled thermal and hydraulic calculation."):
        st.session_state["run_from_circuit_tab"]=True
        st.rerun()
    if not (v["valid"] and v["complete"]):
        st.caption("Complete every circuit and assign every tube to enable a routed analysis. The Design Inputs run button remains available for the equivalent row model.")
    detail_df = route_geometry_table(routes, Pt_mm*MM, Pl_mm*MM)
    with st.expander("Manufacturing pass / return-bend schedule"):
        st.caption("Bend side alternates automatically because each straight tube pass reverses the water direction along the face width.")
        st.dataframe(detail_df.round(3), use_container_width=True, hide_index=True, height=420)
    if IS_ADMIN:
        d1, d2 = st.columns(2)
        d1.download_button(
            "Download circuit routes CSV", detail_df.to_csv(index=False),
            file_name="coil_circuit_routes.csv", mime="text/csv", use_container_width=True,
        )
        d2.download_button(
            "Download circuit routes JSON", json.dumps({str(k): v for k,v in routes.items()}, indent=2),
            file_name="coil_circuit_routes.json", mime="application/json", use_container_width=True,
        )
    else:
        st.caption("Circuit-route file exports are available to the admin account only.")


with result_tab:
    if "cw_result" not in st.session_state:
        st.info("Run the analysis from Design Inputs first.")
    else:
        r = st.session_state["cw_result"]
        t = st.session_state["cw_target"]
        inp = st.session_state["cw_inputs"]
        target_met = st.session_state.get("cw_target_met", True)
        warns = warnings_for_result(r)
        current_routes = {int(k): list(v) for k, v in st.session_state.get("circuit_routes", {}).items()}
        current_route_signature = json.dumps({str(k): v for k, v in sorted(current_routes.items())}, sort_keys=True)
        route_result_stale = current_route_signature != r.get("circuit_route_signature", "")
        if route_result_stale:
            st.error(
                "The circuit map has changed since this analysis was run. These results/PDF are STALE and do not represent the current circuiting. "
                "Return to Design Inputs and click Run chilled-water coil analysis again."
            )
        st.info(f"**Thermal solver actually used:** {r.get('circuit_model','Unknown')}")

        assessment = r.get('target_assessment', {})
        if not route_result_stale:
            if target_met and assessment.get('coolant_pressure_screen_passed') and assessment.get('physical_route_complete'):
                st.success(assessment.get('status', 'Thermal target met.'))
            elif target_met:
                st.warning(assessment.get('status', 'Thermal target met; further checks required.'))
            else:
                st.error(assessment.get('status', 'Design target not met.'))
        if assessment:
            st.caption('Capacity: ' + ('met' if assessment['capacity_met'] else 'not met') +
                       '; leaving-air state: ' +
                       ('matched' if assessment['leaving_air_matched'] else
                        ('not matched' if assessment['air_state_specified'] else 'not specified')) +
                       '; physical circuit: ' + ('complete' if assessment['physical_route_complete'] else 'unverified') + '.')
            if assessment['air_state_specified']:
                st.caption(f"Leaving-air DB difference {assessment['db_error_C']:+.2f} K; humidity-ratio "
                           f"difference {assessment['humidity_error_g_kg']:+.2f} g/kg dry air. "
                           f"Selection matching tolerances: ±{assessment['db_tolerance_C']:.2f} K and "
                           f"±{assessment['humidity_tolerance_g_kg']:.2f} g/kg. "
                           'Air temperatures and humidity *limits* are also reported separately.')

        st.subheader("Thermal performance")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total cooling", f"{r['Q_total_kW']:.2f} kW",
                  f"{r['Q_total_kW']-t['Q_required_kW']:+.2f} kW vs target")
        c2.metric("Sensible cooling", f"{r['Q_sensible_kW']:.2f} kW")
        c3.metric("Latent cooling", f"{r['Q_latent_kW']:.2f} kW")
        c1, c2, c3 = st.columns(3)
        c1.metric("SHR", f"{r['SHR']:.3f}")
        c2.metric("Leaving air DB / WB", f"{r['air_out']['T_C']:.2f} / {r['air_out']['Twb_C']:.2f} degC")
        c3.metric("Leaving air RH", f"{r['air_out']['RH_pct']:.1f}%")
        c1, c2, c3 = st.columns(3)
        c1.metric("Leaving coolant", f"{r['water_out_C']:.2f} degC")
        c2.metric("Condensate", f"{r['condensate_kg_h']:.2f} kg/h")
        c3.metric("Wet fraction", f"{100*r['wet_fraction']:.1f}%")
        c1, c2, c3 = st.columns(3)
        c1.metric("Air dP", f"{r['air_dp_Pa']:.1f} Pa")
        c2.metric("Water dP average", f"{r['hydraulics']['dp_total_avg_kPa']:.2f} kPa")
        c3.metric("Water dP min / max", f"{r['hydraulics']['dp_total_min_kPa']:.1f} / {r['hydraulics']['dp_total_max_kPa']:.1f} kPa")
        margin = r.get("pressure_margin", {})
        if margin:
            st.subheader("Coolant pressure availability")
            a, b, c = st.columns(3)
            a.metric("Supply pressure", f"{margin['supply_kPa_abs']:.1f} kPa abs")
            b.metric("Least favorable outlet", f"{margin['minimum_outlet_kPa_abs']:.1f} kPa abs")
            c.metric("Margin above water vapor reference", f"{margin['margin_over_water_vapor_kPa']:.1f} kPa")
            st.caption(margin['basis'] + " Static lift and other plant losses are outside this coil calculation.")
            if margin['margin_over_water_vapor_kPa'] <= 0:
                st.error("Supply pressure is insufficient for single-phase operation at this calculated circuit pressure loss.")
        st.info(
            f"**Surface state:** {r['surface_mode']}  |  "
            f"**Physical geometry:** CROSS-FLOW  |  **Water row progression:** {r['water_row_progression']}"
        )

        st.subheader("Airflow and coolant velocities")
        c1, c2, c3 = st.columns(3)
        c1.metric("Gross face velocity", f"{r['face_velocity_m_s']:.3f} m/s")
        c2.metric("Max velocity between fins/tubes", f"{r['max_air_velocity_m_s']:.3f} m/s")
        c3.metric("Coolant velocity in one tube", f"{r['water_ht']['velocity_m_s']:.3f} m/s")
        c1, c2, c3 = st.columns(3)
        c1.metric("Supply header velocity", f"{r['hydraulics']['header_supply_velocity_m_s']:.3f} m/s")
        c2.metric("Return header velocity", f"{r['hydraulics']['header_return_velocity_m_s']:.3f} m/s")
        c3.metric("Coolant mass flow / circuit", f"{r['water_ht']['mdot_per_circuit_kg_s']:.3f} kg/s")

        st.subheader("Heat-transfer diagnostics")
        c1, c2, c3 = st.columns(3)
        c1.metric("C air", f"{r['C_air_kW_K']:.3f} kW/K")
        c2.metric("C coolant", f"{r['C_coolant_kW_K']:.3f} kW/K")
        c3.metric("Capacity ratio Cr", f"{r['Cr']:.3f}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Dry-reference NTU", f"{r['NTU_dry']:.3f}")
        c2.metric("Dry cross-flow effectiveness", f"{r['effectiveness_dry_crossflow']:.3f}")
        c3.metric("Wet enthalpy effectiveness", f"{r['wet_enthalpy_effectiveness']:.3f}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Air Re / Pr", f"{r['air_corr']['Re_air']:.0f} / {r['air_corr']['Pr_air']:.3f}")
        c2.metric("Coolant Re / Pr", f"{r['water_ht']['Re_water']:.0f} / {r['water_ht']['Pr_water']:.3f}")
        c3.metric("Air-temperature effectiveness", f"{r['air_temperature_effectiveness']:.3f}")
        c1, c2 = st.columns(2)
        with c1:
            st.info(
                f"**Heat-capacity-rate limiting stream:** {r['capacity_rate_limiting_side']}  \n"
                "This is the Cmin stream in the epsilon-NTU sense."
            )
        with c2:
            rs = r["resistance_split_pct"]
            st.info(
                f"**Thermal-resistance limiting side:** {r['resistance_limiting_side']}  \n"
                f"Resistance split: air {rs['air']:.1f}% - water {rs['water']:.1f}% - wall {rs['wall']:.1f}%"
            )

        st.subheader("Row-by-row thermal marching")
        st.caption(
            f"Physical flow is CROSS-FLOW in every row. {r['water_row_progression']}. "
            f"Reverse row profile converged: {r['row_march_converged']} in {r['row_march_iterations']} iteration(s)."
        )
        row_df = r["row_table"].copy()
        display_cols = [
            "Row_air_sequence", "Air_in_DB_C", "Air_out_DB_C", "Air_out_WB_C", "Air_out_RH_pct",
            "Water_in_C", "Water_out_C", "Q_total_kW", "Q_latent_kW", "Wet_fraction_pct", "Surface_mode",
        ]
        st.dataframe(row_df[display_cols].round(3), use_container_width=True, hide_index=True, height=300)
        with st.expander("Show full row diagnostics"):
            st.dataframe(row_df.round(4), use_container_width=True, hide_index=True, height=420)

        with st.expander("Water-circuit pressure-drop paths"):
            st.caption(f"Circuit model: {r.get('circuit_model', 'Equal-flow circuit-count model')}")
            st.dataframe(r["hydraulics"]["table"].round(3), use_container_width=True, hide_index=True)
            if "flow_imbalance_pct_max" in r["hydraulics"]:
                st.write(
                    f"Explicit network convergence: {r['hydraulics'].get('converged')} in {r['hydraulics'].get('iterations')} iteration(s); "
                    f"maximum circuit flow deviation from equal flow = {r['hydraulics']['flow_imbalance_pct_max']:.2f}%."
                )

        if r.get("circuit_temperature"):
            st.subheader("Physical circuiting - tube-by-tube coolant temperature")
            if r.get("thermal_model", "").startswith("Fully coupled"):
                st.success(
                    "The physical circuit map is active in the thermal calculation. Every tube uses its own entering coolant temperature and circuit flow; "
                    "its leaving coolant feeds the next routed tube, while its leaving air feeds the next row in the same vertical air lane."
                )
            else:
                st.info("Physical circuit temperatures are shown, but the current result is using the equivalent row-bank model.")
            cto = r["circuit_temperature"]
            c1, c2 = st.columns(2)
            c1.metric("Circuit-resolved mixed leaving coolant", f"{cto['mixed_outlet_C']:.2f} degC")
            c2.metric("Maximum circuit flow imbalance", f"{r['hydraulics'].get('flow_imbalance_pct_max',0):.2f}%")
            st.dataframe(cto["circuit_outlet_table"].round(4), use_container_width=True, hide_index=True)
            with st.expander("Show every tube pass temperature"):
                st.dataframe(cto["tube_table"].round(4), use_container_width=True, hide_index=True, height=480)

        if r.get("cell_table") is not None:
            st.subheader("Fully coupled 2-D tube / air-lane solution")
            st.caption(
                f"Model: {r.get('thermal_model','')} | converged: {r.get('tube2d_converged')} in {r.get('tube2d_iterations')} thermal iteration(s) | "
                f"energy-balance error {r.get('energy_balance_error_pct',0):.4f}%."
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Minimum circuit tube velocity", f"{r['water_ht'].get('velocity_min_m_s', r['water_ht']['velocity_m_s']):.3f} m/s")
            c2.metric("Maximum circuit tube velocity", f"{r['water_ht'].get('velocity_max_m_s', r['water_ht']['velocity_m_s']):.3f} m/s")
            c3.metric("2-D cells solved", f"{len(r['cell_table'])}")
            with st.expander("Show every tube's local air and coolant conditions", expanded=False):
                show_cols = [
                    "Circuit","Sequence","Tube","Row","Tube_position","Circuit_mass_flow_kg_s",
                    "Water_in_C","Water_out_C","Air_in_DB_C","Air_out_DB_C","Air_out_WB_C","Air_out_RH_pct",
                    "Q_total_kW","Q_latent_kW","Wet_fraction_pct","Tube_velocity_m_s","Re_water","Re_air",
                ]
                st.dataframe(r["cell_table"][show_cols].round(4), use_container_width=True, hide_index=True, height=520)
            st.info(
                "Current 2-D model assumes equal entering dry-air mass flow in each vertical tube lane and no lateral air redistribution. "
                "Cross-fin conduction between adjacent tubes is not yet included; this is the next higher-order refinement when adjacent tube temperatures differ strongly."
            )

        st.subheader("Calculated geometry and selected materials")
        gcalc = r["geometry"]
        geo_df = pd.DataFrame({
            "Item": [
                "Tube and fin orientation", "Tube cross-section", "Fin construction", "Fin type", "Fin material", "Tube material", "FPI", "Fin pitch",
                "Number of rows", "Tubes per row", "Total tubes", "Selected circuits", "Circuit model", "Tube length", "Fin count",
                "Tube major / minor outside axes", "Tube internal flow area / hydraulic diameter", "Fin strips between tubes", "Tube holes / plate", "Gross fin area / plate", "Tube hole area / plate", "Net fin area / plate", "Gross tube area", "Tube area covered by fins", "Face / free-flow area", "Free-area ratio", "External air-side area (total)", "Net fin area", "Exposed tube outside area", "Inside tube heat-transfer area",
            ],
            "Value": [
                gcalc['orientation'],inp["tube_shape"],inp["fin_construction"],inp["fin_type"], inp["fin_material"], inp["tube_material"], f"{inp['FPI']:.1f} 1/in",
                f"{inp['fin_pitch_mm']:.3f} mm", inp["rows"], gcalc["n_tubes_per_row"], gcalc["n_tubes_total"],
                inp["circuits"], r.get("circuit_model", "Equal-flow circuit-count model"),
                f"{gcalc['tube_length_m']:.3f} m", gcalc["n_fins"],
                f"{gcalc['tube_major_axis_m']*1000:.3f} / {gcalc['tube_minor_axis_m']*1000:.3f} mm",
                f"{gcalc['inside_flow_area_m2']:.7f} m2 / {gcalc['Di_m']*1000:.3f} mm",
                gcalc['serpentine_strip_count'],gcalc['fin_holes_each'],
                f"{gcalc['fin_gross_area_each_m2']:.5f} m2",f"{gcalc['fin_hole_area_each_m2']:.5f} m2",
                f"{gcalc['fin_net_area_each_m2']:.5f} m2",f"{gcalc['A_tube_outer_full_m2']:.4f} m2",
                f"{gcalc['tube_area_covered_by_fins_m2']:.4f} m2",
                f"{gcalc['face_area_m2']:.3f} / {gcalc['free_flow_area_m2']:.3f} m2",
                f"{gcalc['free_area_ratio']:.3f}", f"{gcalc['A_air_total_m2']:.2f} m2",
                f"{gcalc['A_fin_m2']:.2f} m2", f"{gcalc['A_bare_m2']:.2f} m2",
                f"{gcalc['A_i_total_m2']:.2f} m2",
            ],
        })
        st.dataframe(geo_df, use_container_width=True, hide_index=True)

        st.subheader("Design Improvement Guidance")
        rs = r["resistance_split_pct"]
        guidance = []
        if margin and margin['margin_over_water_vapor_kPa'] <= 0:
            guidance.append("Increase coil supply pressure or reduce circuit and header losses; confirm pump suction and plant elevation effects.")
        if not target_met:
            if rs["air"] >= 60.0:
                if r["face_velocity_m_s"] > 2.5 or r["air_dp_Pa"] > 180.0:
                    guidance.append("Increase coil face area first: it lowers face/core velocity and air dP while adding effective surface area.")
                else:
                    guidance.append("Add tube rows (for example +1 or +2 rows) or increase face area; the air side is the dominant thermal resistance.")
            if rs["water"] >= 35.0 or r["water_ht"]["Re_water"] < 3000:
                if r["water_ht"]["velocity_m_s"] < 2.0:
                    guidance.append("Increase coolant flow if pump head is available; water-side resistance/Reynolds number indicates useful gain is still possible.")
                else:
                    guidance.append("Do not simply increase coolant flow: tube velocity is already high. Increase parallel circuits or tube ID and recheck dP.")
            if r["hydraulics"]["dp_total_avg_kPa"] > 100.0:
                guidance.append("Water dP is already high. Prefer more parallel circuits and/or larger headers before increasing coolant flow.")
            if not guidance:
                guidance.append("Increase heat-transfer area (face area and/or rows) and rerun; current operating point does not satisfy the full target.")
        else:
            guidance.append("No capacity increase is required for the stated target. Optimize rows/face area/circuit count only if lower pressure drop, cost, or size is desired.")
        for item in guidance:
            st.write("- " + item)

        if warns:
            st.subheader("Engineering checks")
            for item in warns:
                st.warning(item)

        st.subheader("Downloads")
        output_pdf = build_output_pdf(inp, r, t, st.session_state.username)

        if IS_ADMIN:
            st.caption("Admin access: standard Output Report plus detailed engineering and data exports.")
            a1, a2 = st.columns(2)
            a1.download_button(
                "Download Output Report (PDF)", output_pdf,
                file_name=f"chilled_water_coil_output_report_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf", use_container_width=True, disabled=route_result_stale,
            )
            detailed_pdf = build_pdf(inp, r, t, warns, st.session_state.username)
            a2.download_button(
                "Download Detailed Engineering Report (PDF)", detailed_pdf,
                file_name=f"chilled_water_coil_detailed_engineering_report_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf", use_container_width=True, disabled=route_result_stale,
            )

            _exclude = {"hydraulics", "row_table", "cell_table", "circuit_temperature", "hydraulics_equal_flow_reference", "circuit_validation"}
            summary = {k: v for k, v in r.items() if k not in _exclude}
            summary["inputs"] = inp
            summary["target"] = t
            summary["hydraulics"] = {k: v for k, v in r["hydraulics"].items() if k != "table"}
            if r["hydraulics"].get("table") is not None:
                summary["hydraulic_circuit_table"] = r["hydraulics"]["table"].to_dict(orient="records")
            if r.get("circuit_validation"):
                cv = r["circuit_validation"]
                summary["circuit_validation_summary"] = {
                    "valid": cv.get("valid"), "complete": cv.get("complete"), "balanced": cv.get("balanced"),
                    "assigned_count": cv.get("assigned_count"), "total_tubes": cv.get("total_tubes"),
                    "errors": cv.get("errors", []), "warnings": cv.get("warnings", []),
                    "pass_counts": cv.get("pass_counts", []),
                }
            if r.get("circuit_temperature"):
                ct = r["circuit_temperature"]
                summary["circuit_temperature_summary"] = {
                    "mixed_outlet_C": ct.get("mixed_outlet_C"),
                    "method_note": ct.get("method_note"),
                    "circuit_outlets": ct["circuit_outlet_table"].to_dict(orient="records"),
                }

            c1, c2 = st.columns(2)
            c1.download_button(
                "Download Detailed Summary JSON", json.dumps(summary, indent=2, default=float),
                file_name=f"chilled_water_coil_detailed_{datetime.now():%Y%m%d_%H%M}.json",
                mime="application/json", use_container_width=True, disabled=route_result_stale,
            )
            c2.download_button(
                "Download Row-by-Row CSV", row_df.to_csv(index=False),
                file_name=f"coil_rows_{datetime.now():%Y%m%d_%H%M}.csv",
                mime="text/csv", use_container_width=True, disabled=route_result_stale,
            )
            d1, d2 = st.columns(2)
            cell_export = r.get("cell_table")
            hyd_export = r.get("hydraulics", {}).get("table")
            d1.download_button(
                "Download Tube-by-Tube Thermal CSV",
                (cell_export.to_csv(index=False) if cell_export is not None else "No explicit tube-by-tube circuit model in this run."),
                file_name=f"coil_tube_by_tube_{datetime.now():%Y%m%d_%H%M}.csv",
                mime="text/csv", use_container_width=True,
                disabled=route_result_stale or cell_export is None,
            )
            d2.download_button(
                "Download Circuit Hydraulics CSV",
                (hyd_export.to_csv(index=False) if hyd_export is not None else "No hydraulic circuit table."),
                file_name=f"coil_circuit_hydraulics_{datetime.now():%Y%m%d_%H%M}.csv",
                mime="text/csv", use_container_width=True,
                disabled=route_result_stale or hyd_export is None,
            )
        else:
            st.caption("Engineer access: only the standard Output Report is downloadable. Detailed engineering calculations and data exports are restricted to admin.")
            st.download_button(
                "Download Output Report (PDF)", output_pdf,
                file_name=f"chilled_water_coil_output_report_{datetime.now():%Y%m%d_%H%M}.pdf",
                mime="application/pdf", use_container_width=True, disabled=route_result_stale,
            )

with method_tab:
    st.markdown(
        """
### Flow geometry - important terminology correction

A normal AHU chilled-water fin-and-tube coil is a **cross-flow heat exchanger** at the local tube level: air travels through the coil depth and coolant travels along the tube axis, so the two velocity directions are approximately 90 degrees.

The older labels **parallel flow** and **counterflow** were misleading when shown without qualification. In v2.1 they have been renamed **cross-parallelflow row progression** and **cross-counterflow row progression**. They describe only which side of the coil receives the cold-water connection; they do not rotate the physical water flow into the air-flow direction.

### Fin families

- **Plain fin:** Wang, Chi & Chang (2000) plain fin-and-tube j/f correlation.
- **Wavy fin:** calibration-required Wang plain-fin baseline using the corrected physical fin/tube area; waviness is not counted again as an artificial area multiplier.
- **Wavy + louvers:** Wang-Tsai-Lu wavy/louvered j/f correlation as documented by ACHP.

### Water side

CoolProp supplies water/MEG/MPG properties. Smooth-tube heat transfer uses Gnielinski with a transition treatment; tube and header losses use Darcy-Weisbach with Churchill friction and configurable bend/branch K values.

### Row model

Air is marched serially from the entering face to the leaving face. For water entering the air-leaving side, the opposing row-water temperature profile is iterated to convergence. Each local dry row uses a **cross-flow effectiveness relation**. The wet calculation uses the established enthalpy-potential wet/dry approach; exact tube-by-tube temperatures still require an explicit physical circuit routing map.

### Physical circuiting editor

v2.5.4 uses the manufacturing circuit map directly in the thermal solution. A tube is identified by row and vertical position, for example `R6-T1`. A circuit is an ordered list of tube passes joined by return bends. The app checks duplicate/missing tubes, each circuit's pass count, same-end/even-pass and opposite-end/odd-pass compatibility, and long bend spans. **Equal pass counts are preferred but are not required.** A complete route activates the circuit-resolved header/friction network and the fully coupled tube-by-tube thermal solver.

When a complete route is defined, the app switches to a fully coupled tube-by-tube / air-lane iteration. Each R#-T# cell receives the local air state from the previous row and the local coolant temperature from the previous tube in its circuit. The cell is solved as a local cross-flow wet/dry heat exchanger; both outlet states are then propagated and the whole grid is iterated to convergence. Unequal circuit lengths are allowed when every circuit retains the required even/odd outlet-end parity. The hydraulic network calculates the resulting unequal flows instead of assuming equal distribution.

The current 2-D model still neglects lateral cross-fin conduction and assumes uniform entering-air mass flow among the vertical lanes. Those are explicit remaining refinements rather than hidden assumptions.

### Production validation

This is engineering design software, not an AHRI-certified selection program. Air-side h and dP, wet dP, return-bend K values and header takeoff losses should be calibrated against your actual coil tooling and test/selection data before guaranteed manufacturing selections.
"""
    )
