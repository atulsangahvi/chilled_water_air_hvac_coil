import io
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


def _style_table(tbl, fontsize=7):
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.35, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), fontsize),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    return tbl


def build_pdf(inputs: dict, result: dict, target: dict | None, warnings: list[str], username: str) -> bytes:
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=26, bottomMargin=26)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.5, leading=9.5))
    story = [Paragraph("Chilled Water Cooling Coil Design Report — v2", styles["Title"]),
             Paragraph(f"Prepared by user: {username}", styles["Small"]), Spacer(1, 8)]

    perf = [
        ["Item", "Value"],
        ["Total capacity", f"{result['Q_total_kW']:.2f} kW"],
        ["Sensible / latent", f"{result['Q_sensible_kW']:.2f} / {result['Q_latent_kW']:.2f} kW"],
        ["SHR", f"{result['SHR']:.3f}"],
        ["Leaving air DB / WB", f"{result['air_out']['T_C']:.2f} / {result['air_out']['Twb_C']:.2f} °C"],
        ["Leaving air RH", f"{result['air_out']['RH_pct']:.1f} %"],
        ["Leaving coolant", f"{result['water_out_C']:.2f} °C"],
        ["Condensate", f"{result['condensate_kg_h']:.2f} kg/h"],
        ["Wet fraction", f"{100*result['wet_fraction']:.1f} %"],
        ["Air ΔP", f"{result['air_dp_Pa']:.1f} Pa"],
        ["Water ΔP avg", f"{result['hydraulics']['dp_total_avg_kPa']:.2f} kPa"],
        ["Water ΔP min / max", f"{result['hydraulics']['dp_total_min_kPa']:.2f} / {result['hydraulics']['dp_total_max_kPa']:.2f} kPa"],
    ]
    if target:
        perf.extend([
            ["Target mode", str(target.get('mode', ''))],
            ["Target capacity", f"{target['Q_required_kW']:.2f} kW"],
            ["Capacity margin", f"{result['Q_total_kW']-target['Q_required_kW']:.2f} kW"],
        ])
        if target.get("mode") == "Leaving air condition":
            perf.append(["Target leaving DB / WB / RH",
                         f"{target['target_db_C']:.2f} °C / {target['target_WB_C']:.2f} °C / {target['target_RH_pct']:.1f}%"])
    story += [Paragraph("Performance", styles["Heading2"]), _style_table(Table(perf, colWidths=[170, 315]), 8), Spacer(1, 9)]

    diag = [
        ["Heat-transfer diagnostic", "Value"],
        ["C air", f"{result['C_air_kW_K']:.3f} kW/K"],
        ["C coolant", f"{result['C_water_kW_K']:.3f} kW/K"],
        ["Cmin / Cmax", f"{result['Cmin_kW_K']:.3f} / {result['Cmax_kW_K']:.3f} kW/K"],
        ["Cr", f"{result['capacity_ratio_Cr']:.3f}"],
        ["Dry-reference NTU / effectiveness", f"{result['NTU_dry_reference']:.3f} / {result['effectiveness_dry_reference']:.3f}"],
        ["Wet enthalpy effectiveness", f"{result['effectiveness_enthalpy_wet']:.3f}"],
        ["Air temperature effectiveness", f"{result['effectiveness_air_temperature']:.3f}"],
        ["Air Re / Pr", f"{result['air_corr']['Re_air']:.0f} / {result['air_corr']['Pr_air']:.3f}"],
        ["Coolant Re / Pr", f"{result['water_ht']['Re_water']:.0f} / {result['water_ht']['Pr_water']:.3f}"],
        ["Capacity-rate limiting side", result['capacity_rate_limiting_side']],
        ["Resistance limiting side", result['resistance_limiting_side']],
        ["Resistance split air / water / wall",
         f"{100*result['R_air_fraction']:.1f}% / {100*result['R_water_fraction']:.1f}% / {100*result['R_wall_fraction']:.1f}%"],
    ]
    story += [Paragraph("Heat Transfer Diagnostics", styles["Heading2"]), _style_table(Table(diag, colWidths=[210, 275]), 7.5), Spacer(1, 9)]

    vel = [
        ["Velocity / flow item", "Value"],
        ["Air face velocity", f"{result['face_velocity_m_s']:.3f} m/s"],
        ["Max air velocity between fins/tubes", f"{result['core_max_velocity_m_s']:.3f} m/s"],
        ["Coolant velocity in one tube", f"{result['water_ht']['velocity_m_s']:.3f} m/s"],
        ["Supply header velocity", f"{result['hydraulics']['header_supply_velocity_m_s']:.3f} m/s"],
        ["Return header velocity", f"{result['hydraulics']['header_return_velocity_m_s']:.3f} m/s"],
    ]
    story += [Paragraph("Velocities", styles["Heading2"]), _style_table(Table(vel, colWidths=[230, 255]), 7.5), Spacer(1, 9)]

    g = result["geometry"]
    geo_rows = [
        ["Geometry", "Value"],
        ["Tubes/row", str(g["n_tubes_per_row"])],
        ["Total tubes", str(g["n_tubes_total"])],
        ["Tube length", f"{g['tube_length_m']:.3f} m"],
        ["Fin count", str(g["n_fins"])],
        ["Face / free-flow area", f"{g['face_area_m2']:.3f} / {g['free_flow_area_m2']:.3f} m²"],
        ["Free-area ratio", f"{g['free_area_ratio']:.3f}"],
        ["Air-side area", f"{g['A_air_total_m2']:.2f} m²"],
        ["Inside tube area", f"{g['A_i_total_m2']:.2f} m²"],
    ]
    story += [Paragraph("Calculated Geometry", styles["Heading2"]), _style_table(Table(geo_rows, colWidths=[180, 305]), 7.5), Spacer(1, 9)]

    if warnings:
        story.append(Paragraph("Engineering Warnings", styles["Heading2"]))
        for x in warnings:
            story.append(Paragraph("• " + x, styles["Small"]))
        story.append(Spacer(1, 8))

    if "row_table" in result and len(result["row_table"]) > 0:
        story.append(PageBreak())
        story.append(Paragraph("Row-by-Row Thermal Marching", styles["Heading2"]))
        story.append(Paragraph(
            f"Rows are numbered in the air-flow direction. Thermal arrangement: {result.get('water_thermal_arrangement','')}. "
            f"Converged: {result.get('row_marching_converged', False)} in {result.get('row_marching_iterations', 0)} iterations.",
            styles["Small"],
        ))
        df = result["row_table"]
        cols = ["Row_air_sequence", "Air_in_DB_C", "Air_out_DB_C", "Air_out_WB_C", "Air_out_RH_pct",
                "Water_in_C", "Water_out_C", "Q_total_kW", "Wet_fraction_pct", "Surface_mode"]
        data = [["Row", "Air DB in", "Air DB out", "Air WB out", "RH out %", "Water in", "Water out", "Q kW", "Wet %", "Mode"]]
        for _, rr in df[cols].iterrows():
            data.append([
                int(rr["Row_air_sequence"]), f"{rr['Air_in_DB_C']:.2f}", f"{rr['Air_out_DB_C']:.2f}",
                f"{rr['Air_out_WB_C']:.2f}", f"{rr['Air_out_RH_pct']:.1f}", f"{rr['Water_in_C']:.2f}",
                f"{rr['Water_out_C']:.2f}", f"{rr['Q_total_kW']:.2f}", f"{rr['Wet_fraction_pct']:.1f}", str(rr["Surface_mode"]),
            ])
        story.append(_style_table(Table(data, repeatRows=1, colWidths=[24,45,48,48,40,45,45,38,38,70]), 5.5))
        story.append(Spacer(1, 8))

    story += [Paragraph("Method / Validation Note", styles["Heading2"]),
              Paragraph(
                  "The v2 model marches air row-by-row and iterates the opposing coolant row temperatures for the counterflow option. "
                  "This is an equivalent row-bank model; exact tube-by-tube coolant temperatures require the physical circuit routing map. "
                  "Air-side heat transfer and dry pressure drop use the Wang–Tsai–Lu wavy/louvered fin correlation as documented by ACHP. "
                  "Water-side heat transfer uses Gnielinski with a transition treatment; tube/header pressure loss uses Darcy–Weisbach with Churchill friction. "
                  "Wet-coil capacity uses a segmented enthalpy-potential dry/part-wet/wet treatment. Validate air HTC, wet air ΔP and fitting K values against actual coil test data before production release.",
                  styles["Small"])]
    doc.build(story)
    return bio.getvalue()
