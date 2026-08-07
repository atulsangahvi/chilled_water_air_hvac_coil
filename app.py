from __future__ import annotations
import json
from datetime import datetime
import math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Chilled Water Coil Designer v2", page_icon="💧", layout="wide")

from auth import require_login, logout
from coil_core import (
    CoilGeometry, AirCondition, HydraulicInputs, MM,
    thermal_performance, warnings_for_result, coolant_props,
    air_state_from_db_wb, target_load_from_condition, target_capacity,
    target_is_met, design_recommendations,
)
from reporting import build_pdf

CFM_TO_M3S = 0.00047194745

require_login()

with st.sidebar:
    st.success(f"Logged in: {st.session_state.username} ({st.session_state.role})")
    if st.button("Logout"):
        logout()
    st.divider()
    st.caption("Engineering model v2.0 — segmented chilled-water wet/dry fin-tube coil")
    st.caption("Row thermal marching is an equivalent bank model. Exact tube-by-tube temperatures require the physical circuit routing map.")

st.title("💧 Chilled Water Cooling Coil Designer v2")
st.caption("Wet/dry cooling • row-by-row air & coolant temperatures • air/water ΔP • design target checking • multi-user Streamlit")

input_tab, result_tab, method_tab = st.tabs(["📐 Design Inputs", "📊 Results", "📚 Method & Validation"])

with input_tab:
    st.subheader("1. Design target")
    target_mode = st.radio(
        "What should the coil be designed/checked against?",
        ["Required cooling capacity (kW)", "Required leaving-air condition"],
        horizontal=True,
    )
    if target_mode == "Required cooling capacity (kW)":
        target_kW = st.number_input("Required total cooling capacity (kW)", 0.10, 5000.0, 35.0, 0.5)
        leaving_target_mode = None
        target_T = target_secondary = None
    else:
        c1, c2, c3 = st.columns(3)
        leaving_target_mode = c1.radio("Leaving-air target input", ["DB + RH", "DB + WB"], horizontal=False)
        target_T = c2.number_input("Required leaving DB (°C)", -5.0, 40.0, 13.0, 0.1)
        if leaving_target_mode == "DB + RH":
            target_secondary = c3.number_input("Required leaving RH (%)", 5.0, 100.0, 90.0, 0.5)
        else:
            target_secondary = c3.number_input("Required leaving WB (°C)", -10.0, float(target_T), min(12.0, float(target_T)), 0.1)

    st.subheader("2. Coil geometry")
    c1,c2,c3,c4 = st.columns(4)
    face_W = c1.number_input("Face width / tube length (m)", 0.20, 6.0, 1.20, 0.01)
    face_H = c2.number_input("Face height (m)", 0.20, 4.0, 0.85, 0.01)
    rows = c3.number_input("Rows deep", 1, 16, 6, 1)
    FPI = c4.number_input("Fins per inch (FPI)", 4.0, 24.0, 10.0, 0.5)

    c1,c2,c3,c4 = st.columns(4)
    Pt_mm = c1.number_input("Transverse / vertical tube pitch Pt (mm)", 12.0, 60.0, 25.4, 0.1)
    Pl_mm = c2.number_input("Longitudinal / row pitch Pl (mm)", 10.0, 60.0, 22.0, 0.1)
    Do_mm = c3.number_input("Tube OD (mm)", 5.0, 20.0, 9.53, 0.01)
    tw_mm = c4.number_input("Tube wall thickness (mm)", 0.20, 2.0, 0.35, 0.01)

    c1,c2,c3,c4 = st.columns(4)
    tf_mm = c1.number_input("Fin thickness (mm)", 0.06, 0.30, 0.12, 0.01)
    fin_mat = c2.selectbox("Fin material", ["Aluminum", "Copper"], index=0)
    tube_mat = c3.selectbox("Tube material", ["Copper", "CuNi 90/10"], index=0)
    c4.selectbox("Fin family", ["Wavy / louvered (Wang correlation)"], index=0)

    with st.expander("Advanced fin geometry / calibration"):
        a1,a2,a3,a4 = st.columns(4)
        wave_2a_mm = a1.number_input("Wave parameter Pd / twice amplitude (mm)", 0.10, 5.0, 1.0, 0.05)
        wave_half_mm = a2.number_input("Wave half-period xf (mm)", 0.10, 10.0, 1.0, 0.05)
        h_mult = a3.number_input("Air HTC calibration multiplier", 0.50, 2.00, 1.00, 0.01)
        dp_mult = a4.number_input("Dry air ΔP calibration multiplier", 0.50, 3.00, 1.00, 0.01)
        wet_dp_factor = st.number_input(
            "Wet/dry air ΔP ratio for wetted portion", 0.70, 3.00, 1.12, 0.01,
            help="Calibration input. Condensate retention and drainability can materially change wet pressure drop.",
        )
        b1,b2 = st.columns(2)
        Rfo = b1.number_input("Air-side fouling resistance (m²·K/W)", 0.0, 0.003, 0.0, 0.00005, format="%.5f")
        Rfi = b2.number_input("Water-side fouling resistance (m²·K/W)", 0.0, 0.003, 0.0, 0.00005, format="%.5f")

    st.subheader("3. Entering air condition and airflow")
    c1,c2,c3,c4 = st.columns(4)
    air_condition_mode = c1.radio("Entering air input", ["DB + RH", "DB + WB"], horizontal=False)
    Tair_in = c2.number_input("Entering air DB (°C)", -5.0, 60.0, 27.0, 0.1)
    if air_condition_mode == "DB + RH":
        air_secondary = c3.number_input("Entering air RH (%)", 5.0, 100.0, 50.0, 0.5)
    else:
        air_secondary = c3.number_input("Entering air WB (°C)", -10.0, float(Tair_in), min(19.0, float(Tair_in)), 0.1)
    P_air_kPa = c4.number_input("Air pressure (kPa abs)", 70.0, 120.0, 101.325, 0.1)

    # Convert DB+WB to RH because AirCondition stores a normalized DB+RH state.
    if air_condition_mode == "DB + WB":
        try:
            air_from_wb = air_state_from_db_wb(Tair_in, air_secondary, P_air_kPa*1000)
            RHair_in = air_from_wb["RH_pct"]
            entering_WB = air_secondary
            st.caption(f"Calculated entering RH = {RHair_in:.1f}%")
        except Exception:
            RHair_in = 50.0
            entering_WB = air_secondary
    else:
        RHair_in = air_secondary
        entering_WB = None

    st.markdown("**Airflow input**")
    c1,c2,c3,c4,c5 = st.columns(5)
    flow_mode = c1.selectbox("Airflow unit", ["Face velocity (m/s)", "m³/s", "m³/h", "CFM"])
    face_area = face_W * face_H
    if flow_mode == "Face velocity (m/s)":
        flow_value = c2.number_input("Face velocity", 0.10, 8.0, 2.0, 0.05)
        Vdot = flow_value * face_area
    elif flow_mode == "m³/s":
        flow_value = c2.number_input("Air volume (m³/s)", 0.02, 100.0, 2.04, 0.01)
        Vdot = flow_value
    elif flow_mode == "m³/h":
        flow_value = c2.number_input("Air volume (m³/h)", 50.0, 500000.0, 7344.0, 50.0)
        Vdot = flow_value / 3600.0
    else:
        flow_value = c2.number_input("Air volume (CFM)", 50.0, 300000.0, 4322.0, 10.0)
        Vdot = flow_value * CFM_TO_M3S
    face_v = Vdot / max(face_area, 1e-12)
    c3.metric("m³/s", f"{Vdot:.3f}")
    c4.metric("m³/h", f"{Vdot*3600:.0f}")
    c5.metric("CFM", f"{Vdot/CFM_TO_M3S:.0f}")
    st.caption(f"Calculated face velocity = {face_v:.3f} m/s")

    st.subheader("4. Chilled water / glycol")
    c1,c2,c3,c4 = st.columns(4)
    coolant = c1.selectbox("Coolant", ["Water", "Ethylene Glycol", "Propylene Glycol"], index=0)
    glycol = c2.number_input("Glycol concentration (% mass)", 0.0, 60.0, 0.0 if coolant=="Water" else 25.0, 1.0, disabled=(coolant=="Water"))
    Tw_in = c3.number_input("Entering chilled-water/coolant temperature (°C)", -10.0, 25.0, 7.0, 0.1)
    water_pressure_kPa = c4.number_input("Coolant pressure (kPa abs)", 100.0, 2000.0, 300.0, 10.0)

    c1,c2,c3,c4 = st.columns(4)
    water_input = c1.radio("Coolant flow input", ["Volume flow (m³/h)", "Mass flow (kg/s)"], horizontal=False)
    if water_input == "Mass flow (kg/s)":
        mdot_w = c2.number_input("Total coolant mass flow (kg/s)", 0.02, 200.0, 1.50, 0.01)
        try:
            rho_ui = coolant_props(coolant, glycol, Tw_in, water_pressure_kPa*1000)["rho"]
        except Exception:
            rho_ui = 1000.0
        Vw_m3h = mdot_w / rho_ui * 3600.0
    else:
        Vw_m3h = c2.number_input("Total coolant flow (m³/h)", 0.05, 1000.0, 5.40, 0.05)
        try:
            rho_ui = coolant_props(coolant, glycol, Tw_in, water_pressure_kPa*1000)["rho"]
        except Exception:
            rho_ui = 1000.0
        mdot_w = (Vw_m3h / 3600.0) * rho_ui
    circuits = c3.number_input("Parallel water circuits", 1, 300, 12, 1)
    c4.metric("Flow / circuit", f"{mdot_w/int(circuits):.3f} kg/s")
    st.caption(f"Total coolant flow ≈ {Vw_m3h:.3f} m³/h | {Vw_m3h/3.6:.3f} L/s")

    water_thermal_arrangement = st.radio(
        "Equivalent thermal flow arrangement",
        ["Counterflow / water enters air-leaving side", "Parallel flow / water enters air-entering side"],
        horizontal=True,
        help="Counterflow is normally preferred for cooling-coil thermal performance. Exact tube-by-tube marching requires the actual circuit routing map.",
    )

    st.subheader("5. Headers and circuit fitting losses")
    c1,c2,c3,c4 = st.columns(4)
    hdr_in_od_mm = c1.number_input("Inlet/supply header OD (mm)", 10.0, 300.0, 42.4, 0.1)
    hdr_in_t_mm = c2.number_input("Inlet header wall thickness (mm)", 0.5, 15.0, 1.5, 0.1)
    hdr_out_od_mm = c3.number_input("Outlet/return header OD (mm)", 10.0, 300.0, 42.4, 0.1)
    hdr_out_t_mm = c4.number_input("Outlet header wall thickness (mm)", 0.5, 15.0, 1.5, 0.1)
    c1,c2,c3,c4 = st.columns(4)
    hdr_L = c1.number_input("Header length (m)", 0.10, 10.0, face_H, 0.05)
    hdr_arr = c2.selectbox("Header outlet arrangement", ["Opposite-end (reverse-return tendency)", "Same-end"], index=0)
    bend_K = c3.number_input("Return bend K per bend", 0.0, 10.0, 1.5, 0.1)
    branch_K = c4.number_input("Circuit takeoff/return K", 0.0, 10.0, 0.5, 0.1)

    with st.expander("Additional hydraulic coefficients"):
        c1,c2,c3,c4 = st.columns(4)
        entry_K = c1.number_input("Common inlet K", 0.0, 10.0, 0.5, 0.1)
        exit_K = c2.number_input("Common outlet K", 0.0, 10.0, 1.0, 0.1)
        tube_rough_um = c3.number_input("Tube absolute roughness (µm)", 0.1, 100.0, 1.5, 0.1)
        hdr_rough_um = c4.number_input("Header absolute roughness (µm)", 0.1, 100.0, 1.5, 0.1)

    MAT_K = {"Aluminum":205.0, "Copper":380.0, "CuNi 90/10":29.0}
    geom_obj = CoilGeometry(face_W, face_H, int(rows), Pt_mm*MM, Pl_mm*MM, Do_mm*MM, tw_mm*MM,
                            FPI, tf_mm*MM, MAT_K[fin_mat], MAT_K[tube_mat], wave_2a_mm*MM, wave_half_mm*MM)
    air_obj = AirCondition(Tair_in, RHair_in, P_air_kPa*1000)
    hyd_obj = HydraulicInputs(int(circuits), mdot_w, hdr_in_od_mm*MM, hdr_in_t_mm*MM,
                              hdr_out_od_mm*MM, hdr_out_t_mm*MM, hdr_L, hdr_arr,
                              tube_rough_um*1e-6, hdr_rough_um*1e-6, bend_K, branch_K, entry_K, exit_K)

    if st.button("🚀 Run chilled-water coil analysis", type="primary", use_container_width=True):
        try:
            if target_mode == "Required cooling capacity (kW)":
                target = target_capacity(target_kW)
            else:
                target = target_load_from_condition(air_obj, Vdot, target_T, target_secondary, leaving_target_mode)

            res = thermal_performance(
                geom_obj, air_obj, Vdot, coolant, glycol, Tw_in, water_pressure_kPa*1000,
                hyd_obj, h_mult, dp_mult, wet_dp_factor, Rfo, Rfi, water_thermal_arrangement,
            )
            recs = design_recommendations(
                res, target, geom_obj, air_obj, Vdot, coolant, glycol, Tw_in, water_pressure_kPa*1000,
                hyd_obj, h_mult, dp_mult, wet_dp_factor, Rfo, Rfi, water_thermal_arrangement,
            )

            st.session_state["cw_result"] = res
            st.session_state["cw_target"] = target
            st.session_state["cw_recommendations"] = recs
            st.session_state["cw_inputs"] = {
                "target_mode": target_mode,
                "face_width_m":face_W,"face_height_m":face_H,"rows":int(rows),"Pt_mm":Pt_mm,"Pl_mm":Pl_mm,
                "tube_OD_mm":Do_mm,"tube_wall_mm":tw_mm,"FPI":FPI,"fin_thickness_mm":tf_mm,
                "circuits":int(circuits),"airflow_m3_s":Vdot,"airflow_m3_h":Vdot*3600,"airflow_CFM":Vdot/CFM_TO_M3S,
                "face_velocity_m_s":face_v,"air_in_DB_C":Tair_in,"air_in_RH_pct":RHair_in,
                "water_in_C":Tw_in,"water_mdot_kg_s":mdot_w,"water_flow_m3_h":Vw_m3h,
                "coolant":coolant,"glycol_pct":glycol,"water_pressure_kPa_abs":water_pressure_kPa,
                "header_supply_OD_mm":hdr_in_od_mm,"header_supply_t_mm":hdr_in_t_mm,
                "header_return_OD_mm":hdr_out_od_mm,"header_return_t_mm":hdr_out_t_mm,
                "header_arrangement":hdr_arr,"thermal_arrangement":water_thermal_arrangement,
            }
            st.success("Analysis complete. Open the Results tab.")
        except Exception as e:
            st.exception(e)

with result_tab:
    if "cw_result" not in st.session_state:
        st.info("Run the analysis from Design Inputs first.")
    else:
        r = st.session_state["cw_result"]
        t = st.session_state["cw_target"]
        recs = st.session_state["cw_recommendations"]
        inp = st.session_state["cw_inputs"]
        warns = warnings_for_result(r)
        met = target_is_met(r, t)
        margin = r["Q_total_kW"] - float(t["Q_required_kW"])

        if met:
            st.success("✅ Selected coil meets the design target.")
        else:
            st.error("❌ Selected coil does not meet the design target. See the Design Improvement Suggestions below.")

        st.subheader("Thermal performance")
        cols = st.columns(6)
        cols[0].metric("Total cooling", f"{r['Q_total_kW']:.2f} kW", f"{margin:+.2f} kW vs target")
        cols[1].metric("Sensible", f"{r['Q_sensible_kW']:.2f} kW")
        cols[2].metric("Latent", f"{r['Q_latent_kW']:.2f} kW")
        cols[3].metric("SHR", f"{r['SHR']:.3f}")
        cols[4].metric("Leaving air DB/WB", f"{r['air_out']['T_C']:.2f} / {r['air_out']['Twb_C']:.2f} °C")
        cols[5].metric("Leaving air RH", f"{r['air_out']['RH_pct']:.1f}%")
        cols = st.columns(6)
        cols[0].metric("Leaving coolant", f"{r['water_out_C']:.2f} °C")
        cols[1].metric("Condensate", f"{r['condensate_kg_h']:.2f} kg/h")
        cols[2].metric("Wet fraction", f"{100*r['wet_fraction']:.1f}%")
        cols[3].metric("Surface state", r['surface_mode'])
        cols[4].metric("Air ΔP", f"{r['air_dp_Pa']:.1f} Pa")
        cols[5].metric("Water ΔP avg", f"{r['hydraulics']['dp_total_avg_kPa']:.2f} kPa")

        if t.get("mode") == "Leaving air condition":
            st.info(
                f"Target leaving air: **{t['target_db_C']:.2f}°C DB / {t['target_WB_C']:.2f}°C WB / "
                f"{t['target_RH_pct']:.1f}% RH** • equivalent required load **{t['Q_required_kW']:.2f} kW**"
            )
        else:
            st.info(f"Required total cooling capacity: **{t['Q_required_kW']:.2f} kW**")

        st.subheader("Airflow and coolant velocities")
        cols = st.columns(6)
        cols[0].metric("Face velocity", f"{r['face_velocity_m_s']:.3f} m/s")
        cols[1].metric("Max velocity between fins/tubes", f"{r['core_max_velocity_m_s']:.3f} m/s")
        cols[2].metric("Tube coolant velocity", f"{r['water_ht']['velocity_m_s']:.3f} m/s")
        cols[3].metric("Supply header velocity", f"{r['hydraulics']['header_supply_velocity_m_s']:.3f} m/s")
        cols[4].metric("Return header velocity", f"{r['hydraulics']['header_return_velocity_m_s']:.3f} m/s")
        cols[5].metric("Coolant flow/circuit", f"{r['water_ht']['mdot_per_circuit_kg_s']:.3f} kg/s")

        st.subheader("Heat-transfer diagnostics")
        cols = st.columns(6)
        cols[0].metric("C air", f"{r['C_air_kW_K']:.3f} kW/K")
        cols[1].metric("C coolant", f"{r['C_water_kW_K']:.3f} kW/K")
        cols[2].metric("Cmin / Cmax", f"{r['Cmin_kW_K']:.3f} / {r['Cmax_kW_K']:.3f} kW/K")
        cols[3].metric("Capacity ratio Cr", f"{r['capacity_ratio_Cr']:.3f}")
        cols[4].metric("Dry-reference NTU", f"{r['NTU_dry_reference']:.3f}")
        cols[5].metric("Dry-reference ε", f"{r['effectiveness_dry_reference']:.3f}")
        cols = st.columns(6)
        cols[0].metric("Wet enthalpy effectiveness", f"{r['effectiveness_enthalpy_wet']:.3f}")
        cols[1].metric("Air temperature effectiveness", f"{r['effectiveness_air_temperature']:.3f}")
        cols[2].metric("Air Re", f"{r['air_corr']['Re_air']:.0f}")
        cols[3].metric("Air Pr", f"{r['air_corr']['Pr_air']:.3f}")
        cols[4].metric("Coolant Re", f"{r['water_ht']['Re_water']:.0f}")
        cols[5].metric("Coolant Pr", f"{r['water_ht']['Pr_water']:.3f}")

        c1,c2 = st.columns(2)
        c1.info(
            f"**Heat-capacity-rate limiting side:** {r['capacity_rate_limiting_side']}  \n"
            f"This identifies which stream is Cmin in the ε–NTU sense."
        )
        c2.info(
            f"**Thermal-resistance limiting side:** {r['resistance_limiting_side']}  \n"
            f"Resistance split: air {100*r['R_air_fraction']:.1f}% • water {100*r['R_water_fraction']:.1f}% • wall {100*r['R_wall_fraction']:.1f}%"
        )

        st.subheader("Row-by-row thermal marching")
        st.caption(
            f"Arrangement: {r['water_thermal_arrangement']} • convergence: "
            f"{'yes' if r['row_marching_converged'] else 'no'} in {r['row_marching_iterations']} iterations. "
            "Row number is in the air-flow direction."
        )
        st.dataframe(r["row_table"].round(4), use_container_width=True, hide_index=True, height=430)

        st.subheader("Pressure-drop breakdown by water circuit")
        st.dataframe(r["hydraulics"]["table"].round(4), use_container_width=True, hide_index=True)
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Minimum path ΔP", f"{r['hydraulics']['dp_total_min_kPa']:.2f} kPa")
        c2.metric("Maximum path ΔP", f"{r['hydraulics']['dp_total_max_kPa']:.2f} kPa")
        c3.metric("Supply header ID", f"{r['hydraulics']['header_supply_ID_mm']:.2f} mm")
        c4.metric("Return header ID", f"{r['hydraulics']['header_return_ID_mm']:.2f} mm")

        st.subheader("Calculated coil geometry")
        g = r["geometry"]
        geo_df = pd.DataFrame({
            "Item":["Tubes per row","Total tubes","Tube length","Fin count","Face area","Free-flow area","Free-area ratio","Air-side area","Inside tube area"],
            "Value":[g['n_tubes_per_row'],g['n_tubes_total'],f"{g['tube_length_m']:.3f} m",g['n_fins'],f"{g['face_area_m2']:.3f} m²",
                     f"{g['free_flow_area_m2']:.3f} m²",f"{g['free_area_ratio']:.3f}",f"{g['A_air_total_m2']:.2f} m²",f"{g['A_i_total_m2']:.2f} m²"]
        })
        st.dataframe(geo_df, use_container_width=True, hide_index=True)

        st.subheader("Design improvement suggestions")
        if met:
            st.success("The present geometry/flow meets the target; no capacity increase is required.")
        else:
            st.warning(
                "The alternatives below change **one variable at a time**. They are ranked by the smallest relative input change, "
                "not by manufacturing cost or lifecycle cost. Check the resulting air and water pressure-drop penalties before selecting one."
            )
        st.dataframe(recs.round(3), use_container_width=True, hide_index=True)
        if not met and len(recs) > 0 and bool(recs.iloc[0].get("Target_met", False)):
            first = recs.iloc[0]
            st.info(f"Smallest one-variable change found: **{first['Option']} — {first['Change']}**.")

        if warns:
            st.subheader("Engineering checks")
            for x in warns:
                st.warning(x)

        st.subheader("Downloads")
        c1,c2,c3,c4 = st.columns(4)
        summary = {k:v for k,v in r.items() if k not in ["hydraulics", "row_table"]}
        summary["hydraulics"] = {k:v for k,v in r["hydraulics"].items() if k != "table"}
        c1.download_button(
            "Download summary JSON", json.dumps(summary, indent=2, default=float),
            file_name=f"chilled_water_coil_{datetime.now():%Y%m%d_%H%M}.json", mime="text/json", use_container_width=True,
        )
        c2.download_button(
            "Download row marching CSV", r["row_table"].to_csv(index=False),
            file_name=f"coil_rows_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv", use_container_width=True,
        )
        c3.download_button(
            "Download circuit ΔP CSV", r["hydraulics"]["table"].to_csv(index=False),
            file_name=f"coil_circuit_dp_{datetime.now():%Y%m%d_%H%M}.csv", mime="text/csv", use_container_width=True,
        )
        pdf = build_pdf(inp, r, t, warns, st.session_state.username)
        c4.download_button(
            "Download PDF report", pdf, file_name=f"chilled_water_coil_report_{datetime.now():%Y%m%d_%H%M}.pdf",
            mime="application/pdf", use_container_width=True,
        )

with method_tab:
    st.markdown("""
### v2 calculation structure

**Row-by-row thermal marching is now included.** Air is marched row 1 → row N. For the normal counterflow option, chilled water is coupled in the opposite direction and the row inlet temperatures are iterated until the air/water row solution converges. The table therefore reports entering/leaving DB, WB, RH, humidity ratio, chilled-water temperatures and row capacity for every bank.

This is an **equivalent row-bank model**, not a tube-by-tube circuit map. If you later provide the exact circuit routing (which tube connects to which return bend and which header branch), the next level can march coolant tube-by-tube and predict individual circuit outlet temperatures/maldistribution.

### Calculation basis

- **Air side:** Wang–Tsai–Lu wavy/louvered fin Colburn-j and friction correlation as documented by ACHP. The app reports both face velocity and maximum velocity through the calculated minimum free-flow area.
- **Wet coil:** segmented dry/part-wet/wet enthalpy-potential calculation. Each row can change wetness as air and water temperatures change.
- **Water/coolant side:** CoolProp water/MEG/MPG properties, Gnielinski turbulent heat transfer with transition treatment, Darcy–Weisbach pressure loss and Churchill friction.
- **Circuit hydraulics:** integer tube counts per circuit, straight-tube loss, return bends, branch losses and distributed header pressure loss.
- **Thermal diagnostics:** air and coolant heat-capacity rates C=ṁcp, Cmin, Cmax, Cr, dry-reference NTU/effectiveness, wet enthalpy effectiveness, Reynolds and Prandtl numbers, and thermal-resistance split.
- **Design suggestions:** if the selected coil misses the target, the program checks added rows, higher coolant flow, and larger face area one variable at a time and shows the resulting capacity and pressure drops.

### Interpretation of "limiting side"

The app intentionally reports **two different limits**. `Capacity-rate limiting side` means the stream with the smaller C=ṁcp. `Thermal-resistance limiting side` means the side contributing the larger part of 1/UA. They are not necessarily the same side.

### Validation status

This is a design model, not AHRI-certified selection software. Correlation-based air-side heat transfer and especially wet air pressure drop must ultimately be calibrated against your actual fin tooling, collars, drainage, headers, bends and test data before manufacturing release.
""")
