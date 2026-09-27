"""Interactive Plotly cross-section for selecting physical coil tube passes."""
from __future__ import annotations

import plotly.graph_objects as go

from circuiting import parse_tube_id, tube_id


PALETTE = ("#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c",
           "#0891b2", "#be123c", "#4f46e5", "#65a30d", "#a16207")


def tube_plot(rows: int, tubes_per_row: int, routes: dict[int, list[str]]) -> go.Figure:
    """Return a selectable tube cross-section; tube markers are the final trace."""
    fig = go.Figure()
    owner = {}
    for circuit, route in sorted(routes.items()):
        color = PALETTE[(circuit - 1) % len(PALETTE)]
        for sequence, label in enumerate(route, 1):
            owner[label] = (circuit, sequence)
        if len(route) > 1:
            coords = [parse_tube_id(label) for label in route]
            fig.add_trace(go.Scatter(
                x=[r for r, _ in coords], y=[t for _, t in coords],
                mode="lines", line=dict(color=color, width=2),
                hoverinfo="skip", showlegend=False,
            ))

    ids = [tube_id(r, t) for t in range(1, tubes_per_row + 1)
           for r in range(1, rows + 1)]
    locations = [parse_tube_id(label) for label in ids]
    colors = [PALETTE[(owner[label][0] - 1) % len(PALETTE)]
              if label in owner else "#ffffff" for label in ids]
    hover = [f"{label}: Circuit {owner[label][0]}, pass {owner[label][1]}"
             if label in owner else f"{label}: unassigned" for label in ids]
    fig.add_trace(go.Scatter(
        x=[r for r, _ in locations], y=[t for _, t in locations],
        mode="markers+text", customdata=[[label] for label in ids],
        text=[str(owner[label][0]) if label in owner else "" for label in ids],
        textposition="middle center", textfont=dict(color="white", size=9),
        marker=dict(size=18, color=colors, line=dict(color="#475569", width=1.3)),
        hovertext=hover, hovertemplate="%{hovertext}<extra></extra>",
        selected=dict(marker=dict(opacity=1)),
        unselected=dict(marker=dict(opacity=1)),
        showlegend=False, name="Tube passes",
    ))
    fig.update_layout(
        height=min(850, max(280, 100 + tubes_per_row * 23)),
        margin=dict(l=55, r=25, t=50, b=35),
        plot_bgcolor="white", paper_bgcolor="white",
        clickmode="event+select", hovermode="closest",
        xaxis=dict(title="Tube rows (air inlet to outlet →)",
                   tickmode="linear", tick0=1, dtick=1,
                   range=[0.5, rows + 0.5], fixedrange=True,
                   showgrid=True, gridcolor="#e2e8f0"),
        yaxis=dict(title="Tube positions (top to bottom ↓)",
                   tickmode="linear", tick0=1, dtick=1,
                   range=[tubes_per_row + 0.5, 0.5], fixedrange=True,
                   showgrid=True, gridcolor="#e2e8f0"),
    )
    return fig


def selected_tube(selection, marker_trace_index: int) -> str | None:
    """Read one tube selection and disregard clicks on drawn connection lines."""
    points = selection.get("points", []) if selection else []
    for point in points:
        if point.get("curve_number") != marker_trace_index:
            continue
        value = point.get("customdata")
        if isinstance(value, (tuple, list)) and value:
            value = value[0]
        if isinstance(value, str):
            return value
    return None
