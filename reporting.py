from __future__ import annotations

import io
from typing import Iterable

from circuiting import parse_tube_id

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Circle, Line, String
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


def _safe(x) -> str:
    s = str(x)
    repl = {
        "Δ": "d", "ε": "epsilon", "²": "2", "³": "3", "µ": "u", "μ": "u",
        "·": ".", "–": "-", "—": "-", "→": "->", "°": " deg",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def _table(rows, widths, font=8, repeat=1):
    data = [[_safe(c) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9dde3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font),
        ("LEADING", (0, 0), (-1, -1), font + 2),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#888888")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t



def _circuit_drawing(inputs: dict, result: dict, width_pt: float = 720, height_pt: float = 330):
    routes = result.get("circuit_routes") or {}
    if not routes:
        return None
    rows = int(inputs.get("rows", 0) or 0)
    tpr = int(result.get("geometry", {}).get("n_tubes_per_row", 0) or 0)
    if rows < 1 or tpr < 1:
        return None
    d = Drawing(width_pt, height_pt)
    left, right, top, bottom = 55.0, 70.0, 38.0, 30.0
    dx = (width_pt-left-right)/max(rows-1,1)
    dy = (height_pt-top-bottom)/max(tpr-1,1)
    palette = [
        colors.HexColor("#2563eb"), colors.HexColor("#16a34a"), colors.HexColor("#dc2626"),
        colors.HexColor("#9333ea"), colors.HexColor("#ea580c"), colors.HexColor("#0891b2"),
        colors.HexColor("#be123c"), colors.HexColor("#4f46e5"), colors.HexColor("#65a30d"),
        colors.HexColor("#a16207"),
    ]
    def xy(r,t):
        return left+(r-1)*dx, height_pt-top-(t-1)*dy
    owner={}
    for c, route in routes.items():
        for label in route:
            owner[label]=int(c)
    # Airflow arrow and labels
    d.add(Line(left-35, height_pt-16, width_pt-right+25, height_pt-16, strokeColor=colors.grey, strokeWidth=1.2))
    d.add(String(left-34, height_pt-11, "Entering air", fontSize=7.5, fillColor=colors.HexColor("#475569")))
    d.add(String(width_pt-right-30, height_pt-11, "Leaving air", fontSize=7.5, fillColor=colors.HexColor("#475569")))
    for r in range(1,rows+1):
        x,_=xy(r,1); d.add(String(x-6,height_pt-top+10,f"R{r}",fontSize=7.5,fillColor=colors.HexColor("#334155")))
    for t in range(1,tpr+1):
        _,y=xy(1,t); d.add(String(8,y-2.5,f"T{t}",fontSize=6.5,fillColor=colors.HexColor("#64748b")))
    # route lines
    for c, route in sorted(routes.items(), key=lambda kv:int(kv[0])):
        col=palette[(int(c)-1)%len(palette)]
        pts=[]
        for label in route:
            try:
                rr,tt=parse_tube_id(label); pts.append(xy(rr,tt))
            except Exception:
                pass
        for a,b in zip(pts[:-1],pts[1:]):
            d.add(Line(a[0],a[1],b[0],b[1],strokeColor=col,strokeWidth=1.5))
    for t in range(1,tpr+1):
        for r in range(1,rows+1):
            label=f"R{r}-T{t}"; x,y=xy(r,t); c=owner.get(label)
            fill=palette[(c-1)%len(palette)] if c else colors.white
            stroke=palette[(c-1)%len(palette)] if c else colors.HexColor("#64748b")
            d.add(Circle(x,y,3.2,fillColor=fill,strokeColor=stroke,strokeWidth=0.8))
            if c and tpr <= 35:
                d.add(String(x-1.8,y-1.8,str(c),fontSize=4.3,fillColor=colors.white))
    # legend
    lx=width_pt-right+8; ly=height_pt-top
    for j,c in enumerate(sorted(int(k) for k in routes.keys())[:10]):
        yy=ly-j*14; col=palette[(c-1)%len(palette)]
        d.add(Circle(lx,yy,3.2,fillColor=col,strokeColor=col))
        d.add(String(lx+7,yy-2.5,f"C{c}",fontSize=6.5,fillColor=colors.HexColor("#334155")))
    return d


def build_pdf(inputs: dict, result: dict, target: dict | None, warnings: list[str], username: str) -> bytes:
    """Build a readable mixed portrait/landscape A4 report.

    Summary pages are portrait.  The row-by-row appendix switches to landscape so the user
    does not need tiny fonts.  Major two-column tables are kept with their headings to avoid
    the previous velocity-table split across pages.
    """
    buf = io.BytesIO()
    psize = A4
    lsize = landscape(A4)
    doc = BaseDocTemplate(
        buf,
        pagesize=psize,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Chilled Water Cooling Coil Design Report v2.4.3",
    )

    pframe = Frame(15 * mm, 15 * mm, psize[0] - 30 * mm, psize[1] - 30 * mm, id="portrait_frame")
    lframe = Frame(12 * mm, 12 * mm, lsize[0] - 24 * mm, lsize[1] - 24 * mm, id="landscape_frame")

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 7 * mm, "Chilled Water Cooling Coil Designer v2.4.3")
        canvas.drawRightString(canvas._pagesize[0] - 15 * mm, 7 * mm, f"Page {d.page}")
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="Portrait", pagesize=psize, frames=[pframe], onPage=footer),
        PageTemplate(id="Landscape", pagesize=lsize, frames=[lframe], onPage=footer),
    ])

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleC", parent=styles["Title"], fontSize=18, leading=21, alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontSize=12, leading=14, spaceBefore=6, spaceAfter=5))
    styles.add(ParagraphStyle(name="Smallx", parent=styles["BodyText"], fontSize=8, leading=10, spaceAfter=3))
    styles.add(ParagraphStyle(name="Tiny", parent=styles["BodyText"], fontSize=7, leading=8.5, spaceAfter=2))

    story = [
        Paragraph("Chilled Water Cooling Coil Design Report - v2.4.3", styles["TitleC"]),
        Paragraph(f"Prepared by user: {_safe(username)}", styles["Smallx"]),
        Paragraph(
            "Physical flow geometry: CROSS-FLOW (air perpendicular to tube/coolant direction). "
            + _safe(inputs.get("water_row_progression", result.get("water_row_progression", ""))),
            styles["Smallx"],
        ),
        Spacer(1, 4),
    ]

    input_rows = [
        ["Input / construction", "Value"],
        ["Face W x H", f"{inputs.get('face_width_m', 0):.3f} x {inputs.get('face_height_m', 0):.3f} m"],
        ["Number of tube rows (airflow depth)", inputs.get("rows", "")],
        ["Nominal tube-bank depth", f"{float(inputs.get('rows', 0)) * float(inputs.get('Pl_mm', 0)):.1f} mm"],
        ["Fin type", inputs.get("fin_type", result.get("fin_type", ""))],
        ["Fin material", inputs.get("fin_material", "")],
        ["Tube material", inputs.get("tube_material", "")],
        ["FPI / fin pitch", f"{inputs.get('FPI', 0):.1f} 1/in / {inputs.get('fin_pitch_mm', 0):.3f} mm"],
        ["Fin thickness", f"{inputs.get('fin_thickness_mm', 0):.3f} mm"],
        ["Tube OD / wall", f"{inputs.get('tube_OD_mm', 0):.3f} / {inputs.get('tube_wall_mm', 0):.3f} mm"],
        ["Pt / Pl", f"{inputs.get('Pt_mm', 0):.3f} / {inputs.get('Pl_mm', 0):.3f} mm"],
        ["Airflow", f"{inputs.get('airflow_m3_s', 0):.4f} m3/s = {inputs.get('airflow_m3_h', 0):.0f} m3/h = {inputs.get('airflow_CFM', 0):.0f} CFM"],
        ["Gross face velocity", f"{inputs.get('face_velocity_m_s', result.get('face_velocity_m_s', 0)):.3f} m/s"],
        ["Entering air DB / RH", f"{inputs.get('air_in_DB_C', 0):.2f} degC / {inputs.get('air_in_RH_pct', 0):.1f}%"],
        ["Coolant / concentration", f"{inputs.get('coolant', '')} / {inputs.get('glycol_pct', 0):.1f}%"],
        ["Coolant inlet / pressure", f"{inputs.get('water_in_C', 0):.2f} degC / {inputs.get('water_pressure_kPa_abs', 0):.1f} kPa abs"],
        ["Coolant mass flow / circuits", f"{inputs.get('water_mdot_kg_s', 0):.4f} kg/s / {inputs.get('circuits', '')}"],
        ["Circuit tube-end arrangement", inputs.get("circuit_connection_style", "")],
        ["Calculated tubes / row / total", f"{result.get('geometry',{}).get('n_tubes_per_row','')} / {result.get('geometry',{}).get('n_tubes_total','')}"],
        ["Circuit model", result.get("circuit_model", "Equal-flow circuit-count model")],
        ["Circuit pass counts", ", ".join(str(x) for x in (result.get("circuit_validation") or {}).get("pass_counts", [])) or "Not explicitly routed"],
        ["Water row progression", result.get("water_row_progression", inputs.get("water_row_progression", ""))],
    ]
    story.append(KeepTogether([Paragraph("Design Inputs", styles["H2x"]), _table(input_rows, [62 * mm, 113 * mm])]))
    story.append(Spacer(1, 7))

    perf_rows = [
        ["Performance", "Value"],
        ["Total capacity", f"{result['Q_total_kW']:.2f} kW"],
        ["Sensible / latent", f"{result['Q_sensible_kW']:.2f} / {result['Q_latent_kW']:.2f} kW"],
        ["SHR", f"{result['SHR']:.3f}"],
        ["Leaving air DB / WB", f"{result['air_out']['T_C']:.2f} / {result['air_out']['Twb_C']:.2f} degC"],
        ["Leaving air RH", f"{result['air_out']['RH_pct']:.1f}%"],
        ["Leaving coolant", f"{result['water_out_C']:.2f} degC"],
        ["Condensate", f"{result['condensate_kg_h']:.2f} kg/h"],
        ["Wet fraction / state", f"{100*result['wet_fraction']:.1f}% / {result['surface_mode']}"],
        ["Air dP", f"{result['air_dp_Pa']:.1f} Pa"],
        ["Water dP avg", f"{result['hydraulics']['dp_total_avg_kPa']:.2f} kPa"],
        ["Water dP min / max", f"{result['hydraulics']['dp_total_min_kPa']:.2f} / {result['hydraulics']['dp_total_max_kPa']:.2f} kPa"],
    ]
    if target:
        perf_rows.append(["Target mode", target.get("target_mode", inputs.get("target_mode", ""))])
        perf_rows.append(["Target capacity", f"{target.get('Q_required_kW', 0):.2f} kW"])
        perf_rows.append(["Capacity margin", f"{result['Q_total_kW'] - target.get('Q_required_kW', 0):+.2f} kW"])
        ta = target.get("target_air") or {}
        if ta:
            perf_rows.append(["Target leaving air", f"DB {ta.get('T_C', 0):.2f} degC / WB {ta.get('Twb_C', 0):.2f} degC / RH {ta.get('RH_pct', 0):.1f}%"])
    story.append(KeepTogether([Paragraph("Performance", styles["H2x"]), _table(perf_rows, [62 * mm, 113 * mm])]))
    story.append(PageBreak())

    diag_rows = [
        ["Heat-transfer diagnostic", "Value"],
        ["C air", f"{result.get('C_air_kW_K', 0):.3f} kW/K"],
        ["C coolant", f"{result.get('C_coolant_kW_K', 0):.3f} kW/K"],
        ["Cmin / Cmax", f"{result.get('Cmin_kW_K', 0):.3f} / {result.get('Cmax_kW_K', 0):.3f} kW/K"],
        ["Capacity ratio Cr", f"{result.get('Cr', 0):.3f}"],
        ["Dry-reference NTU", f"{result.get('NTU_dry', 0):.3f}"],
        ["Dry cross-flow effectiveness", f"{result.get('effectiveness_dry_crossflow', 0):.3f}"],
        ["Wet enthalpy effectiveness", f"{result.get('wet_enthalpy_effectiveness', 0):.3f}"],
        ["Air temperature effectiveness", f"{result.get('air_temperature_effectiveness', 0):.3f}"],
        ["Air Re / Pr", f"{result['air_corr']['Re_air']:.0f} / {result['air_corr']['Pr_air']:.3f}"],
        ["Coolant Re / Pr", f"{result['water_ht']['Re_water']:.0f} / {result['water_ht']['Pr_water']:.3f}"],
        ["Capacity-rate limiting side", result.get("capacity_rate_limiting_side", "")],
        ["Resistance limiting side", result.get("resistance_limiting_side", "")],
    ]
    rs = result.get("resistance_split_pct", {})
    if rs:
        diag_rows.append(["Resistance split air / water / wall", f"{rs.get('air',0):.1f}% / {rs.get('water',0):.1f}% / {rs.get('wall',0):.1f}%"])
    if result.get("cell_table") is not None:
        diag_rows.extend([
            ["Thermal model", result.get("thermal_model", "")],
            ["2-D thermal convergence", f"{result.get('tube2d_converged')} / {result.get('tube2d_iterations', 0)} iterations"],
            ["Thermal/hydraulic outer iterations", result.get("tube2d_hydraulic_outer_iterations", 0)],
            ["Energy-balance error", f"{result.get('energy_balance_error_pct', 0):.4f}%"],
        ])
    story.append(KeepTogether([Paragraph("Heat Transfer Diagnostics", styles["H2x"]), _table(diag_rows, [67 * mm, 108 * mm])]))
    story.append(Spacer(1, 8))

    vel_rows = [
        ["Velocity / flow item", "Value"],
        ["Gross air face velocity", f"{result.get('face_velocity_m_s', 0):.3f} m/s"],
        ["Max air velocity between fins/tubes", f"{result.get('max_air_velocity_m_s', result['air_corr'].get('u_max_m_s',0)):.3f} m/s"],
        ["Coolant velocity in one tube (avg)", f"{result['water_ht']['velocity_m_s']:.3f} m/s"],
        ["Coolant tube velocity min / max", f"{result['water_ht'].get('velocity_min_m_s', result['water_ht']['velocity_m_s']):.3f} / {result['water_ht'].get('velocity_max_m_s', result['water_ht']['velocity_m_s']):.3f} m/s"],
        ["Supply header velocity", f"{result['hydraulics']['header_supply_velocity_m_s']:.3f} m/s"],
        ["Return header velocity", f"{result['hydraulics']['header_return_velocity_m_s']:.3f} m/s"],
        ["Coolant mass flow per circuit", f"{result['water_ht']['mdot_per_circuit_kg_s']:.4f} kg/s"],
    ]
    # Keep the heading + whole table together; this specifically fixes the old page-1/page-2 split.
    story.append(KeepTogether([Paragraph("Velocities", styles["H2x"]), _table(vel_rows, [78 * mm, 97 * mm])]))
    story.append(Spacer(1, 8))

    g = result["geometry"]
    geo_rows = [
        ["Calculated geometry", "Value"],
        ["Tube rows in airflow direction", inputs.get("rows", "")],
        ["Nominal tube-bank depth", f"{float(inputs.get('rows', 0)) * float(inputs.get('Pl_mm', 0)):.1f} mm"],
        ["Tubes / row", g["n_tubes_per_row"]],
        ["Total tubes", g["n_tubes_total"]],
        ["Selected circuits", inputs.get("circuits", "")],
        ["Circuit tube-end arrangement", inputs.get("circuit_connection_style", "")],
        ["Circuit model", result.get("circuit_model", "Equal-flow circuit-count model")],
        ["Tube length", f"{g['tube_length_m']:.3f} m"],
        ["Fin count", g["n_fins"]],
        ["Face / free-flow area", f"{g['face_area_m2']:.3f} / {g['free_flow_area_m2']:.3f} m2"],
        ["Free-area ratio", f"{g['free_area_ratio']:.3f}"],
        ["Air-side area", f"{g['A_air_total_m2']:.2f} m2"],
        ["Inside tube area", f"{g['A_i_total_m2']:.2f} m2"],
        ["Air-side correlation", result['air_corr'].get('correlation', '')],
    ]
    story.append(KeepTogether([Paragraph("Calculated Geometry", styles["H2x"]), _table(geo_rows, [70 * mm, 105 * mm])]))

    if warnings:
        story.append(Spacer(1, 6))
        warning_block = [Paragraph("Engineering Warnings", styles["H2x"])]
        for w in warnings:
            warning_block.append(Paragraph("- " + _safe(w), styles["Smallx"]))
        story.append(KeepTogether(warning_block))

    # Explicit physical circuiting appendix, when a complete route is available.
    routes = result.get("circuit_routes") or {}
    route_check = result.get("circuit_validation") or {}
    if routes and route_check.get("complete"):
        story += [NextPageTemplate("Landscape"), PageBreak(), Paragraph("Physical Water Circuiting", styles["H2x"])]
        story.append(Paragraph(
            "Tube IDs use R# for row in the airflow direction and T# for vertical tube position. "
            "Lines connect the ordered straight tube passes; each connection represents a return bend at a tube end.",
            styles["Smallx"],
        ))
        draw = _circuit_drawing(inputs, result)
        if draw is not None:
            story.append(draw)
            story.append(Spacer(1, 5))
        htab = result.get("hydraulics", {}).get("table")
        if htab is not None and len(htab):
            hdr = ["Circuit", "Passes", "Inlet", "Outlet", "Flow kg/s", "Tube v m/s", "Flow dev %", "Total dP kPa"]
            data=[hdr]
            for _,rr in htab.iterrows():
                data.append([
                    int(rr.get("Circuit",0)), int(rr.get("Passes",rr.get("Tubes",0))), rr.get("Inlet_tube","-"), rr.get("Outlet_tube","-"),
                    f"{rr.get('Mass_flow_kg_s',0):.4f}", f"{rr.get('Tube_velocity_m_s',0):.3f}",
                    f"{rr.get('Flow_vs_equal_pct',0):+.1f}", f"{rr.get('Total_path_dP_kPa',0):.2f}",
                ])
            story.append(KeepTogether([
                Paragraph("Circuit hydraulic distribution", styles["H2x"]),
                _table(data, [16*mm,16*mm,23*mm,23*mm,24*mm,22*mm,22*mm,25*mm], font=6.7, repeat=1),
            ]))
        ctemp=result.get("circuit_temperature")
        if ctemp and ctemp.get("circuit_outlet_table") is not None:
            data=[["Circuit","Passes","Flow kg/s","Water out degC","Circuit Q kW"]]
            for _,rr in ctemp["circuit_outlet_table"].iterrows():
                data.append([int(rr["Circuit"]),int(rr["Passes"]),f"{rr['Mass_flow_kg_s']:.4f}",f"{rr['Water_out_C']:.2f}",f"{rr['Circuit_Q_kW']:.2f}"])
            story.append(Spacer(1,5))
            story.append(KeepTogether([
                Paragraph("Circuit outlet temperatures", styles["H2x"]),
                _table(data,[22*mm,22*mm,30*mm,32*mm,30*mm],font=7.2,repeat=1),
                Paragraph(_safe(ctemp.get("method_note","")), styles["Tiny"]),
            ]))

    cell_df = result.get("cell_table")
    if cell_df is not None and len(cell_df):
        story += [NextPageTemplate("Landscape"), PageBreak(), Paragraph("Fully Coupled Tube-by-Tube Thermal Results", styles["H2x"])]
        story.append(Paragraph(
            "Each R#-T# cell is solved with its own entering air state, entering coolant temperature and resolved circuit flow. "
            "Leaving air feeds the next row in the same vertical air lane; leaving coolant feeds the next tube in that circuit. "
            f"Converged: {result.get('tube2d_converged')} in {result.get('tube2d_iterations', 0)} thermal iteration(s).",
            styles["Smallx"],
        ))
        hdr = ["Ckt", "Seq", "Tube", "Tw in", "Tw out", "Air DB in", "Air DB out", "Air WB out", "Q kW", "Wet %"]
        data = [hdr]
        for _, rr in cell_df.iterrows():
            data.append([
                int(rr.get("Circuit", 0)), int(rr.get("Sequence", 0)), rr.get("Tube", ""),
                f"{rr.get('Water_in_C',0):.2f}", f"{rr.get('Water_out_C',0):.2f}",
                f"{rr.get('Air_in_DB_C',0):.2f}", f"{rr.get('Air_out_DB_C',0):.2f}",
                f"{rr.get('Air_out_WB_C',0):.2f}", f"{rr.get('Q_total_kW',0):.3f}",
                f"{rr.get('Wet_fraction_pct',0):.1f}",
            ])
        story.append(_table(data, [12*mm, 12*mm, 21*mm, 18*mm, 18*mm, 20*mm, 20*mm, 20*mm, 17*mm, 15*mm], font=6.2, repeat=1))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Model assumption: equal entering dry-air mass flow per vertical tube lane. Lateral air redistribution and cross-fin conduction between adjacent tubes are not included in v2.4.3.",
            styles["Tiny"],
        ))

    row_df = result.get("row_table")
    if row_df is not None and len(row_df):
        story += [NextPageTemplate("Landscape"), PageBreak(), Paragraph("Row-by-Row Thermal Marching", styles["H2x"])]
        story.append(Paragraph(
            "Rows are numbered in the air-flow direction. Physical geometry is CROSS-FLOW at every row. "
            + _safe(result.get("water_row_progression", ""))
            + f". Converged: {result.get('row_march_converged', True)} in {result.get('row_march_iterations', 1)} iteration(s). "
            + ("Row values are mixed/aggregated from the individual tube air lanes." if result.get("cell_table") is not None else ""),
            styles["Smallx"],
        ))
        cols = [
            "Row_air_sequence", "Air_in_DB_C", "Air_out_DB_C", "Air_out_WB_C", "Air_out_RH_pct",
            "Water_in_C", "Water_out_C", "Q_total_kW", "Q_latent_kW", "Wet_fraction_pct", "Surface_mode",
        ]
        header = ["Row", "Air DB in", "Air DB out", "Air WB out", "RH out %", "Water in", "Water out", "Q kW", "Latent kW", "Wet %", "Mode"]
        rows = [header]
        for _, rr in row_df.iterrows():
            rows.append([
                int(rr[cols[0]]), f"{rr[cols[1]]:.2f}", f"{rr[cols[2]]:.2f}", f"{rr[cols[3]]:.2f}",
                f"{rr[cols[4]]:.1f}", f"{rr[cols[5]]:.2f}", f"{rr[cols[6]]:.2f}",
                f"{rr[cols[7]]:.2f}", f"{rr[cols[8]]:.2f}", f"{rr[cols[9]]:.1f}", rr[cols[10]],
            ])
        widths = [10*mm, 18*mm, 18*mm, 18*mm, 17*mm, 18*mm, 18*mm, 17*mm, 18*mm, 15*mm, 35*mm]
        story.append(_table(rows, widths, font=7.2, repeat=1))

    story += [NextPageTemplate("Portrait"), PageBreak(), Paragraph("Method / Validation Note", styles["H2x"])]
    notes = [
        "The physical fin-and-tube coil is cross-flow: air moves through the depth and coolant moves along the tubes, approximately 90 degrees to the air velocity.",
        "The water-row-progression option only changes which coil-depth side receives cold water. It is not a physical parallel-flow/counterflow change of the local air/tube orientation.",
        "Plain fins use the Wang, Chi & Chang (2000) plain fin j/f correlation. Wavy+louvered fins use the Wang-Tsai-Lu correlation as documented by ACHP.",
        "Wavy-only mode is deliberately labelled a calibration-required baseline rather than inventing unverified correlation coefficients.",
        "Water-side heat transfer uses Gnielinski with transition treatment; tube/header pressure loss uses Darcy-Weisbach with Churchill friction.",
        "With a complete circuit map, v2.4.3 uses a fully coupled tube-by-tube / air-lane model. Each tube cell receives its own entering coolant temperature and resolved circuit flow and feeds its outlet states to the next coolant pass and next air row.",
        "Unequal circuit pass counts are allowed when each circuit retains the required same-end/even or opposite-end/odd outlet parity. The explicit hydraulic solver calculates the resulting flow maldistribution rather than assuming equal flow.",
        "The 2-D model assumes equal entering dry-air mass flow among vertical lanes and does not yet include lateral cross-fin conduction between adjacent tubes; these are explicit higher-order refinements.",
        "Validate air HTC, wet air dP and fitting K values against actual coil test or trusted manufacturer data before production release.",
    ]
    for n in notes:
        story.append(Paragraph("- " + _safe(n), styles["Smallx"]))

    doc.build(story)
    return buf.getvalue()
