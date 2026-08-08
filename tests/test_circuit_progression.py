from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from circuiting import auto_serpentine_routes, validate_routes


def test_three_row_opposite_end_counter_tendency_starts_at_leaving_row():
    style = "Opposite tube ends (odd passes/circuit required)"
    prog = "Water enters air-leaving side (cross-counterflow tendency)"
    routes = auto_serpentine_routes(3, 22, 22, style, prog)
    check = validate_routes(routes, 3, 22, 22, style, 0.0254, 0.022)
    assert check["valid"] and check["complete"]
    assert set(check["pass_counts"]) == {3}
    assert all(route[0].startswith("R3-") for route in routes.values())
    assert all(route[-1].startswith("R1-") for route in routes.values())


def test_four_row_same_end_counter_tendency_starts_at_leaving_row():
    style = "Same tube end (even passes/circuit required)"
    prog = "Water enters air-leaving side (cross-counterflow tendency)"
    routes = auto_serpentine_routes(4, 22, 22, style, prog)
    check = validate_routes(routes, 4, 22, 22, style, 0.0254, 0.022)
    assert check["valid"] and check["complete"]
    assert set(check["pass_counts"]) == {4}
    assert all(route[0].startswith("R4-") for route in routes.values())
    assert all(route[-1].startswith("R1-") for route in routes.values())
