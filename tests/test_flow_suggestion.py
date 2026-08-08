import math
from coil_core import suggested_coolant_flow_for_velocity

def test_flow_suggestion_one_mps_matches_parallel_tube_flow_area():
    r = suggested_coolant_flow_for_velocity(0.00953, 0.00035, 22, 1.0, 997.0)
    Di = 0.00953 - 2*0.00035
    expected = math.pi*Di**2/4 * 22
    assert abs(r['volume_flow_m3_s'] - expected) < 1e-12
    assert abs(r['volume_flow_m3_h'] - expected*3600) < 1e-10
    assert abs(r['mass_flow_kg_s'] - expected*997.0) < 1e-10
