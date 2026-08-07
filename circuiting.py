from __future__ import annotations

"""Physical tube-circuit routing helpers for the chilled-water coil designer.

The tube map uses:
    R1 ... RN  = tube rows in the AIRFLOW direction (entering-air to leaving-air)
    T1 ... TM  = tube positions from top to bottom of the coil face

A circuit is an ordered list such as:
    R6-T1 -> R5-T1 -> R4-T2 -> ...
Each item represents one straight tube pass. Consecutive items are joined by a return bend.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple
import html
import math
import re

import numpy as np
import pandas as pd


_TUBE_RE = re.compile(r"^R\s*(\d+)\s*[-_/ ]?\s*T\s*(\d+)$", re.I)


def tube_id(row: int, tube: int) -> str:
    return f"R{int(row)}-T{int(tube)}"


def parse_tube_id(value: str) -> Tuple[int, int]:
    m = _TUBE_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"Invalid tube reference '{value}'. Use R#-T#, for example R6-T1.")
    return int(m.group(1)), int(m.group(2))


def all_tube_ids(rows: int, tubes_per_row: int) -> List[str]:
    return [tube_id(r, t) for t in range(1, tubes_per_row + 1) for r in range(1, rows + 1)]


def circuit_counts(total_tubes: int, circuits: int) -> List[int]:
    """Simple arithmetic split used only as a reference.

    The manufacturing circuit generator below uses ``parity_compatible_counts`` because
    all circuits connected to the same pair of header ends must satisfy the same even/odd
    pass parity.  A one-pass difference can put the outlet of one circuit on the wrong tube
    end, so the physically preferred unequal pattern normally differs by *two* passes.
    """
    circuits = max(int(circuits), 1)
    q, rem = divmod(int(total_tubes), circuits)
    return [q + (1 if i < rem else 0) for i in range(circuits)]


def parity_compatible_counts(total_tubes: int, circuits: int, connection_style: str) -> List[int] | None:
    """Return the most even all-tubes-used pass counts with correct outlet-end parity.

    Same tube end -> every circuit requires an even number of straight tube passes.
    Opposite tube ends -> every circuit requires an odd number of passes.

    If exact equal circuit lengths are impossible, the returned counts differ by two passes
    where practical (for example 198 tubes / 12 same-end circuits -> 9 x 16-pass circuits
    and 3 x 18-pass circuits).  This is a physically routeable *unequal* circuit set; the
    hydraulic/thermal solvers must then determine whether the resulting maldistribution is
    acceptable.
    """
    total = int(total_tubes)
    n = max(int(circuits), 1)
    same_end = str(connection_style).startswith("Same tube end")
    required_parity = 0 if same_end else 1
    min_count = 2 if same_end else 1
    if total < n * min_count:
        return None
    # Sum of n same-parity integers has fixed parity.  If this fails, all tubes cannot be
    # used while every circuit exits at the selected header end without a special crossover
    # or intentionally dropped tube(s).
    if (total - n * min_count) % 2:
        return None

    avg = total / n
    low = int(math.floor(avg))
    while low >= min_count and low % 2 != required_parity:
        low -= 1
    if low < min_count:
        low = min_count
    # Ensure low does not exceed the average; move down by 2 if necessary.
    while low > avg + 1e-12 and low - 2 >= min_count:
        low -= 2
    high = low + 2
    remainder = total - low * n
    if remainder < 0 or remainder % 2:
        return None
    n_high = remainder // 2
    if n_high > n:
        return None

    counts = [low] * n
    if n_high:
        # Spread the longer circuits over the face instead of clustering them at one end.
        # This is only a route-generation heuristic; the explicit hydraulic solver still
        # calculates the actual branch/header maldistribution.
        used = set()
        for k in range(int(n_high)):
            idx = min(n - 1, int((k + 0.5) * n / n_high))
            while idx in used and idx + 1 < n:
                idx += 1
            while idx in used and idx - 1 >= 0:
                idx -= 1
            used.add(idx)
            counts[idx] = high
    if sum(counts) != total or any((c % 2) != required_parity for c in counts):
        return None
    return counts


def valid_balanced_circuit_counts(total_tubes: int, connection_style: str, max_circuits: int = 300) -> List[int]:
    """Circuit counts that give *equal* passes and the required tube-end parity."""
    same_end = connection_style.startswith("Same tube end")
    out = []
    for c in range(1, min(int(total_tubes), int(max_circuits)) + 1):
        if total_tubes % c:
            continue
        passes = total_tubes // c
        if (passes % 2 == 0) == same_end:
            out.append(c)
    return out


def valid_routeable_circuit_counts(total_tubes: int, connection_style: str, max_circuits: int = 300) -> List[int]:
    """Circuit counts that can use all tubes with common even/odd outlet-end parity."""
    out = []
    for c in range(1, min(int(total_tubes), int(max_circuits)) + 1):
        if parity_compatible_counts(total_tubes, c, connection_style):
            out.append(c)
    return out


def compatibility_summary(total_tubes: int, circuits: int, connection_style: str) -> Dict[str, object]:
    circuits = max(int(circuits), 1)
    equal_counts = circuit_counts(total_tubes, circuits)
    balanced = (total_tubes % circuits == 0)
    passes = (total_tubes // circuits) if balanced else None
    same_end = connection_style.startswith("Same tube end")
    equal_parity_ok = bool(balanced and (((passes % 2) == 0) == same_end))
    recommended = parity_compatible_counts(total_tubes, circuits, connection_style)
    routeable = recommended is not None
    equal_valid = valid_balanced_circuit_counts(total_tubes, connection_style, max_circuits=min(total_tubes, 300))
    routeable_valid = valid_routeable_circuit_counts(total_tubes, connection_style, max_circuits=min(total_tubes, 300))
    near_equal = sorted(equal_valid, key=lambda x: (abs(x - circuits), x))[:8]
    near_routeable = sorted(routeable_valid, key=lambda x: (abs(x - circuits), x))[:8]
    return {
        "total_tubes": int(total_tubes),
        "circuits": circuits,
        "balanced": balanced,
        "tube_counts": equal_counts,
        "passes_per_circuit": passes,
        "parity_ok": equal_parity_ok,
        "fully_balanced_compatible": bool(balanced and equal_parity_ok),
        # Backward-compatible name: now means physically routeable with all tubes, even if
        # the circuit lengths are intentionally unequal.
        "fully_compatible": bool(routeable),
        "routeable_with_unequal_passes": bool(routeable and not (balanced and equal_parity_ok)),
        "recommended_pass_counts": recommended or [],
        "pass_count_min": min(recommended) if recommended else None,
        "pass_count_max": max(recommended) if recommended else None,
        "nearby_valid_circuit_counts": near_equal,
        "nearby_routeable_circuit_counts": near_routeable,
        "connection_style": connection_style,
    }


def auto_serpentine_routes(rows: int, tubes_per_row: int, circuits: int, connection_style: str) -> Dict[int, List[str]]:
    """Create compact nearest-neighbour serpentine routes, including valid unequal routes.

    When exact equal passes are impossible, the generator uses the closest all-tubes-used
    pass pattern that preserves the required outlet-end parity for *every* circuit.  Unequal
    routes are therefore permitted and subsequently evaluated by the explicit hydraulic and
    fully-coupled thermal solvers rather than being rejected a priori.
    """
    total = int(rows) * int(tubes_per_row)
    comp = compatibility_summary(total, circuits, connection_style)
    counts = list(comp.get("recommended_pass_counts") or [])
    if not counts:
        raise ValueError(
            "This tube/circuit/header-end combination cannot use every tube while keeping all "
            "circuits on the required outlet end. Choose another circuit count, use a special "
            "crossover arrangement, or intentionally drop tube(s)."
        )
    global_path: List[str] = []
    for t in range(1, int(tubes_per_row) + 1):
        rr = range(1, int(rows) + 1) if t % 2 else range(int(rows), 0, -1)
        global_path.extend(tube_id(r, t) for r in rr)
    routes: Dict[int, List[str]] = {}
    cursor = 0
    for c, n in enumerate(counts, 1):
        routes[c] = global_path[cursor:cursor + int(n)]
        cursor += int(n)
    if cursor != len(global_path):
        raise RuntimeError("Internal circuit generator did not allocate every tube.")
    return routes

def parse_route_text(text: str) -> List[str]:
    if not str(text).strip():
        return []
    raw = re.split(r"(?:->|→|,|;|\n)+", str(text))
    out = []
    for item in raw:
        s = item.strip().upper().replace(" ", "")
        if not s:
            continue
        r, t = parse_tube_id(s)
        out.append(tube_id(r, t))
    return out


def route_text(route: Iterable[str]) -> str:
    return " -> ".join(route)


def validate_routes(
    routes: Dict[int, List[str]], rows: int, tubes_per_row: int, circuits: int,
    connection_style: str, transverse_pitch_m: float | None = None,
    longitudinal_pitch_m: float | None = None,
) -> Dict[str, object]:
    allowed = set(all_tube_ids(rows, tubes_per_row))
    errors: List[str] = []
    warnings: List[str] = []
    assigned: Dict[str, int] = {}
    rows_data = []
    same_end = connection_style.startswith("Same tube end")

    for c in range(1, int(circuits) + 1):
        route = list(routes.get(c, []))
        for seq, label in enumerate(route, 1):
            try:
                r, t = parse_tube_id(label)
                canon = tube_id(r, t)
            except Exception as exc:
                errors.append(f"Circuit {c}: {exc}")
                continue
            if canon not in allowed:
                errors.append(f"Circuit {c}: {canon} is outside the selected {rows}-row x {tubes_per_row}-tube/row geometry.")
                continue
            if canon in assigned:
                errors.append(f"{canon} is used in both Circuit {assigned[canon]} and Circuit {c}.")
            else:
                assigned[canon] = c
            rows_data.append({"Circuit": c, "Sequence": seq, "Tube": canon, "Row": r, "Tube_position": t})

        if route:
            if same_end and len(route) % 2:
                errors.append(f"Circuit {c} has {len(route)} passes (odd) but same-tube-end headers require an even pass count.")
            if (not same_end) and len(route) % 2 == 0:
                errors.append(f"Circuit {c} has {len(route)} passes (even) but opposite-tube-end headers require an odd pass count.")
        else:
            warnings.append(f"Circuit {c} has no tubes assigned.")

        if transverse_pitch_m and longitudinal_pitch_m and len(route) >= 2:
            spans = []
            for a, b in zip(route[:-1], route[1:]):
                r1, t1 = parse_tube_id(a); r2, t2 = parse_tube_id(b)
                span = math.hypot((t2 - t1) * transverse_pitch_m, (r2 - r1) * longitudinal_pitch_m)
                spans.append(span)
            if spans:
                max_span = max(spans)
                local_pitch = max(float(transverse_pitch_m), float(longitudinal_pitch_m))
                if max_span > 2.5 * local_pitch:
                    warnings.append(
                        f"Circuit {c} has a long return-bend centre spacing of {max_span*1000:.1f} mm; "
                        "check manufacturability and bend tooling."
                    )

    missing = sorted(allowed - set(assigned), key=lambda x: parse_tube_id(x)[::-1])
    if missing:
        warnings.append(f"{len(missing)} of {len(allowed)} tubes are not yet assigned to any circuit.")
    full = len(assigned) == len(allowed) and not errors
    counts = [len(routes.get(c, [])) for c in range(1, int(circuits) + 1)]
    balanced = bool(counts and min(counts) == max(counts) and min(counts) > 0)
    if counts and not balanced:
        warnings.append(
            f"Intentional unequal circuit pass counts: min {min(counts)}, max {max(counts)}. "
            "This is allowed; use the explicit hydraulic and 2-D thermal results to verify the resulting flow, dP and outlet-temperature imbalance."
        )

    return {
        "valid": not errors,
        "complete": full,
        "balanced": balanced,
        "errors": errors,
        "warnings": warnings,
        "assigned_count": len(assigned),
        "total_tubes": len(allowed),
        "unassigned": missing,
        "owner": assigned,
        "route_table": pd.DataFrame(rows_data),
        "pass_counts": counts,
    }


def route_geometry_table(routes: Dict[int, List[str]], Pt_m: float, Pl_m: float) -> pd.DataFrame:
    rows = []
    for c, route in sorted(routes.items()):
        for seq, label in enumerate(route, 1):
            r, t = parse_tube_id(label)
            bend_span = np.nan
            bend_side = "Outlet connection" if seq == len(route) else ("Far end" if seq % 2 == 1 else "Header/supply end")
            if seq < len(route):
                r2, t2 = parse_tube_id(route[seq])
                bend_span = math.hypot((t2-t) * Pt_m, (r2-r) * Pl_m) * 1000.0
            rows.append({
                "Circuit": c, "Sequence": seq, "Tube": label, "Row": r, "Tube_position": t,
                "Bend_after_pass": bend_side, "Bend_to_next_center_mm": bend_span,
            })
    return pd.DataFrame(rows)


def circuit_svg(
    rows: int, tubes_per_row: int, routes: Dict[int, List[str]],
    width: int = 920, max_height: int = 780,
) -> str:
    """Return a responsive SVG side/cross-section preview of physical circuit routing."""
    rows = int(rows); tubes_per_row = int(tubes_per_row)
    left, right, top, bottom = 70, 55, 55, 45
    xspan = max(width - left - right, 200)
    dy = 20 if tubes_per_row <= 30 else max(9, min(18, 600 / max(tubes_per_row - 1, 1)))
    height = int(min(max_height, max(220, top + bottom + dy * max(tubes_per_row - 1, 1))))
    yspan = height - top - bottom
    dx = xspan / max(rows - 1, 1)
    dy2 = yspan / max(tubes_per_row - 1, 1)

    # Accessible high-contrast circuit palette; reused with labels if >10 circuits.
    palette = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#be123c", "#4f46e5", "#65a30d", "#a16207"]
    owner = {}
    seqmap = {}
    for c, route in routes.items():
        for seq, label in enumerate(route, 1):
            owner[label] = c; seqmap[label] = seq

    def xy(r: int, t: int):
        return left + (r - 1) * dx, top + (t - 1) * dy2

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="white" stroke="#cbd5e1"/>')
    # airflow arrow
    parts.append(f'<line x1="{left-45}" y1="24" x2="{width-right+20}" y2="24" stroke="#64748b" stroke-width="2" marker-end="url(#arr)"/>')
    parts.append('<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#64748b"/></marker></defs>')
    parts.append(f'<text x="{left-40}" y="17" font-size="12" fill="#475569">Entering air</text>')
    parts.append(f'<text x="{width-right-55}" y="17" font-size="12" fill="#475569">Leaving air</text>')

    for r in range(1, rows + 1):
        x, _ = xy(r, 1)
        parts.append(f'<text x="{x}" y="{top-14}" text-anchor="middle" font-size="12" font-weight="600" fill="#334155">R{r}</text>')
    # route connections first so dots stay visible
    for c, route in sorted(routes.items()):
        col = palette[(c - 1) % len(palette)]
        pts = []
        for label in route:
            try:
                r, t = parse_tube_id(label); x, y = xy(r, t); pts.append((x, y))
            except Exception:
                continue
        if len(pts) >= 2:
            pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(f'<polyline points="{pstr}" fill="none" stroke="{col}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" opacity="0.82"/>')

    for t in range(1, tubes_per_row + 1):
        _, y = xy(1, t)
        parts.append(f'<text x="{left-16}" y="{y+4:.1f}" text-anchor="end" font-size="10" fill="#64748b">T{t}</text>')
        for r in range(1, rows + 1):
            label = tube_id(r, t); x, y = xy(r, t)
            c = owner.get(label)
            fill = palette[(c-1) % len(palette)] if c else "#ffffff"
            stroke = palette[(c-1) % len(palette)] if c else "#64748b"
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
            if c:
                parts.append(f'<text x="{x:.1f}" y="{y+3.2:.1f}" text-anchor="middle" font-size="7.5" fill="white" font-weight="700">{c}</text>')

    # legend
    lx, ly = width - right + 5, top
    for idx, c in enumerate(sorted(routes)[:10]):
        col = palette[(c - 1) % len(palette)]
        yy = ly + idx * 18
        parts.append(f'<rect x="{lx}" y="{yy-8}" width="10" height="10" rx="2" fill="{col}"/>')
        parts.append(f'<text x="{lx+14}" y="{yy}" font-size="10" fill="#334155">C{c}</text>')
    parts.append('</svg>')
    return "".join(parts)

# ---------- Circuit-resolved hydraulic and water-temperature post-processing ----------

def _friction_factor(Re: float, rel_rough: float) -> float:
    Re = max(float(Re), 1e-9)
    if Re < 2300.0:
        return 64.0 / Re
    A = (2.457 * math.log(1.0 / (((7.0 / Re) ** 0.9) + 0.27 * rel_rough))) ** 16
    B = (37530.0 / Re) ** 16
    return 8.0 * ((8.0 / Re) ** 12 + 1.0 / (A + B) ** 1.5) ** (1.0 / 12.0)


def _tube_path_dp(mdot: float, tube_count: int, L_tube: float, Di: float, rho: float, mu: float,
                  rough: float, bend_K: float, branch_K: float) -> Tuple[float, float, float, float]:
    A = math.pi * Di ** 2 / 4.0
    v = mdot / max(rho * A, 1e-12)
    Re = rho * v * Di / max(mu, 1e-12)
    f = _friction_factor(Re, rough / max(Di, 1e-12))
    dyn = 0.5 * rho * v * v
    straight = f * (tube_count * L_tube / max(Di, 1e-12)) * dyn
    bends = max(tube_count - 1, 0) * bend_K * dyn
    branch = branch_K * dyn
    return straight + bends + branch, v, Re, f


def _pipe_segment_dp_mdot(mdot: float, rho: float, mu: float, D: float, L: float, rough: float) -> float:
    if mdot <= 0 or L <= 0:
        return 0.0
    A = math.pi * D ** 2 / 4.0
    v = mdot / max(rho * A, 1e-12)
    Re = rho * v * D / max(mu, 1e-12)
    f = _friction_factor(Re, rough / max(D, 1e-12))
    return f * (L / max(D, 1e-12)) * 0.5 * rho * v * v


def _header_paths_actual_positions(
    branch_positions_m: List[float], branch_flows: List[float], rho: float, mu: float,
    D: float, L: float, rough: float, terminal: str,
) -> List[float]:
    """Pressure loss from terminal (feed/outlet) to each branch on a straight header.

    ``terminal`` is ``top`` (x=0) or ``bottom`` (x=L). Branch flow is assumed withdrawn
    from the supply header or added to the return header at its specified position. For
    friction magnitude the same cumulative-flow expression applies in either direction.
    """
    L = max(float(L), 1e-9)
    pos = [min(max(float(p), 0.0), L) for p in branch_positions_m]
    points = sorted(set([0.0, L] + pos))
    segs = []
    term_top = str(terminal).lower().startswith("top")
    for a, b in zip(points[:-1], points[1:]):
        mid = 0.5 * (a + b)
        if term_top:
            flow = sum(q for p, q in zip(pos, branch_flows) if p > mid)
        else:
            flow = sum(q for p, q in zip(pos, branch_flows) if p < mid)
        segs.append((a, b, _pipe_segment_dp_mdot(flow, rho, mu, D, b-a, rough)))

    out = []
    for p in pos:
        if term_top:
            out.append(sum(dp for a, b, dp in segs if b <= p + 1e-12))
        else:
            out.append(sum(dp for a, b, dp in segs if a >= p - 1e-12))
    return out


def explicit_circuit_hydraulics(
    routes: Dict[int, List[str]], geom: Dict[str, float], hyd, props: Dict[str, float],
    tubes_per_row: int, header_feed_end: str = "Top",
    circuit_props: List[Dict[str, float]] | None = None,
) -> Dict[str, object]:
    """Solve approximate flow maldistribution for user-defined physical circuit routes.

    The solver iterates circuit flows so the calculated parallel path pressure drops approach
    equality. Header friction uses the actual vertical location of each circuit's first and
    last tube. Tee/dividing/combining losses remain represented by the user-entered branch K.
    This is a practical engineering network model, not a CFD header model.
    """
    N = int(hyd.circuits)
    route_list = [list(routes.get(i, [])) for i in range(1, N+1)]
    if circuit_props is not None and len(circuit_props) != N:
        raise ValueError("circuit_props must contain one property dictionary per circuit.")
    if any(len(r) == 0 for r in route_list):
        raise ValueError("All circuits must have at least one routed tube before circuit-resolved hydraulics can run.")

    Dsup = hyd.inlet_header_od_m - 2.0 * hyd.inlet_header_thickness_m
    Dret = hyd.outlet_header_od_m - 2.0 * hyd.outlet_header_thickness_m
    if Dsup <= 0 or Dret <= 0:
        raise ValueError("Header wall thickness must be less than half of header OD.")
    Lh = float(hyd.header_length_m)

    # Map tube-position index to physical header height. T1 is top.
    def ypos(label: str) -> float:
        _, t = parse_tube_id(label)
        return ((t - 0.5) / max(int(tubes_per_row), 1)) * Lh

    supply_pos = [ypos(r[0]) for r in route_list]
    return_pos = [ypos(r[-1]) for r in route_list]
    supply_terminal = "Top" if str(header_feed_end).startswith("Top") else "Bottom"
    if str(hyd.header_arrangement).startswith("Opposite"):
        return_terminal = "Bottom" if supply_terminal == "Top" else "Top"
    else:
        return_terminal = supply_terminal

    total = float(hyd.water_mass_flow_kg_s)
    flows = np.full(N, total / N, dtype=float)
    common_v_sup = total / max(props["rho"] * math.pi * Dsup**2/4.0, 1e-12)
    common_v_ret = total / max(props["rho"] * math.pi * Dret**2/4.0, 1e-12)
    common_dp = (
        hyd.common_entry_K * 0.5 * props["rho"] * common_v_sup**2
        + hyd.common_exit_K * 0.5 * props["rho"] * common_v_ret**2
    )

    converged = False
    iterations = 0
    last = None
    for it in range(1, 81):
        core = [] ; velocities = [] ; res = [] ; frics = []
        for i_c, (q, route) in enumerate(zip(flows, route_list)):
            pcore = circuit_props[i_c] if circuit_props is not None else props
            dp, v, Re, f = _tube_path_dp(
                q, len(route), geom["tube_length_m"], geom["Di_m"], pcore["rho"], pcore["mu"],
                hyd.tube_roughness_m, hyd.return_bend_K, hyd.branch_takeoff_K,
            )
            core.append(dp); velocities.append(v); res.append(Re); frics.append(f)
        ds = _header_paths_actual_positions(supply_pos, flows.tolist(), props["rho"], props["mu"], Dsup, Lh, hyd.header_roughness_m, supply_terminal)
        dr = _header_paths_actual_positions(return_pos, flows.tolist(), props["rho"], props["mu"], Dret, Lh, hyd.header_roughness_m, return_terminal)
        path_no_common = np.array(core) + np.array(ds) + np.array(dr)
        total_dp = path_no_common + common_dp
        last = (core, velocities, res, frics, ds, dr, total_dp)
        mean_dp = float(np.mean(path_no_common))
        spread = (float(np.max(path_no_common)) - float(np.min(path_no_common))) / max(mean_dp, 1e-9)
        iterations = it
        if spread < 0.005:
            converged = True
            break
        # q ~ sqrt(dp/R); correct toward a common pressure drop and normalize total flow.
        factors = np.sqrt(mean_dp / np.maximum(path_no_common, 1e-9))
        factors = np.clip(factors, 0.70, 1.30)
        proposal = flows * factors
        proposal *= total / max(float(np.sum(proposal)), 1e-12)
        flows = 0.55 * flows + 0.45 * proposal
        flows *= total / max(float(np.sum(flows)), 1e-12)

    # final recomputation at final flows
    core=[]; velocities=[]; res=[]; frics=[]
    for i_c, (q, route) in enumerate(zip(flows, route_list)):
        pcore = circuit_props[i_c] if circuit_props is not None else props
        dp,v,Re,f = _tube_path_dp(q, len(route), geom["tube_length_m"], geom["Di_m"], pcore["rho"], pcore["mu"],
                                  hyd.tube_roughness_m, hyd.return_bend_K, hyd.branch_takeoff_K)
        core.append(dp); velocities.append(v); res.append(Re); frics.append(f)
    ds = _header_paths_actual_positions(supply_pos, flows.tolist(), props["rho"], props["mu"], Dsup, Lh, hyd.header_roughness_m, supply_terminal)
    dr = _header_paths_actual_positions(return_pos, flows.tolist(), props["rho"], props["mu"], Dret, Lh, hyd.header_roughness_m, return_terminal)
    total_dp = np.array(core)+np.array(ds)+np.array(dr)+common_dp

    rows_out=[]
    qeq=total/N
    for i, route in enumerate(route_list):
        rows_out.append({
            "Circuit": i+1,
            "Passes": len(route),
            "Inlet_tube": route[0], "Outlet_tube": route[-1],
            "Mass_flow_kg_s": flows[i],
            "Flow_vs_equal_pct": 100.0*(flows[i]/max(qeq,1e-12)-1.0),
            "Tube_velocity_m_s": velocities[i], "Re": res[i],
            "Core_bends_branch_dP_kPa": core[i]/1000.0,
            "Supply_header_dP_kPa": ds[i]/1000.0,
            "Return_header_dP_kPa": dr[i]/1000.0,
            "Total_path_dP_kPa": total_dp[i]/1000.0,
            "Supply_branch_height_pct": 100.0*supply_pos[i]/Lh,
            "Return_branch_height_pct": 100.0*return_pos[i]/Lh,
        })
    table=pd.DataFrame(rows_out)
    return {
        "table": table,
        "flows_kg_s": flows.tolist(),
        "converged": converged,
        "iterations": iterations,
        "dp_total_avg_kPa": float(np.mean(total_dp)/1000.0),
        "dp_total_min_kPa": float(np.min(total_dp)/1000.0),
        "dp_total_max_kPa": float(np.max(total_dp)/1000.0),
        "header_path_spread_kPa": float((np.max(total_dp)-np.min(total_dp))/1000.0),
        "flow_imbalance_pct_max": float(np.max(np.abs(flows/qeq-1.0))*100.0),
        "header_supply_ID_mm": Dsup*1000.0,
        "header_return_ID_mm": Dret*1000.0,
        "header_supply_velocity_m_s": common_v_sup,
        "header_return_velocity_m_s": common_v_ret,
        "supply_header_feed_end": supply_terminal,
        "return_header_outlet_end": return_terminal,
        "model": "Explicit routed-circuit network with iterative flow balancing",
        "circuit_specific_properties": bool(circuit_props is not None),
    }


def tube_temperature_postprocess(
    routes: Dict[int, List[str]], row_table: pd.DataFrame, tubes_per_row: int,
    circuit_flows_kg_s: List[float], water_in_C: float, coolant_cp_J_kgK: float,
) -> Dict[str, object]:
    """First-order tube-by-tube coolant temperature reconstruction.

    The converged row-bank thermal duty is conserved and divided equally among the tubes in
    each row. The routed circuit flows then march through those tube duties. This is useful
    for circuit balancing and manufacturing review, but it is intentionally labelled a
    post-processor: it does not yet re-solve the local air-side duty after each circuit's
    temperature becomes different.
    """
    qrow = {int(r["Row_air_sequence"]): float(r["Q_total_kW"])*1000.0 for _, r in row_table.iterrows()}
    cp = max(float(coolant_cp_J_kgK), 1e-9)
    out=[]; circ=[]
    for idx, c in enumerate(sorted(routes)):
        flow = max(float(circuit_flows_kg_s[idx]), 1e-12)
        Tw=float(water_in_C); qsum=0.0
        route=routes[c]
        for seq,label in enumerate(route,1):
            r,t=parse_tube_id(label)
            qcell=qrow.get(r,0.0)/max(int(tubes_per_row),1)
            Tout=Tw+qcell/(flow*cp)
            out.append({
                "Circuit":c,"Sequence":seq,"Tube":label,"Row":r,"Tube_position":t,
                "Water_in_C":Tw,"Water_out_C":Tout,"Assigned_tube_Q_W":qcell,
                "Circuit_mass_flow_kg_s":flow,
            })
            Tw=Tout; qsum+=qcell
        circ.append({"Circuit":c,"Passes":len(route),"Mass_flow_kg_s":flow,"Water_out_C":Tw,"Circuit_Q_kW":qsum/1000.0})
    cdf=pd.DataFrame(circ)
    return {
        "tube_table": pd.DataFrame(out),
        "circuit_outlet_table": cdf,
        "mixed_outlet_C": float(np.average(cdf["Water_out_C"], weights=cdf["Mass_flow_kg_s"])) if len(cdf) else water_in_C,
        "method_note": "Row-duty-conserving circuit temperature post-processor; not yet a fully coupled 2-D circuit thermal solve.",
    }
