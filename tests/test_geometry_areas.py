import math
from coil_core import CoilGeometry, geometry_areas


def test_reference_4row_external_area_projected_basis():
    g = CoilGeometry(
        face_width_m=1.100,
        face_height_m=0.5588,
        rows=4,
        transverse_pitch_m=0.0254,
        longitudinal_pitch_m=0.022,
        tube_od_m=0.00953,
        tube_thickness_m=0.00035,
        fpi=12.0,
        fin_thickness_m=0.00012,
        fin_type="Wavy + louvers",
        wave_amplitude_2x_m=0.001,
        wave_half_period_m=0.001,
    )
    a = geometry_areas(g)
    # Geometry-only benchmark: no extra wavy developed-area multiplier.
    assert a["n_fins"] == 520  # condenser v28.7 nearest-whole-fin convention
    assert a["n_tubes_per_row"] == 22
    assert a["n_tubes_total"] == 88
    assert 44.4 < a["A_fin_m2"] < 44.7
    assert 2.70 < a["A_bare_m2"] < 2.76
    assert 47.1 < a["A_air_total_m2"] < 47.4
    # Reference selection reports 46.49 m2; remaining small difference is likely collar/edge convention,
    # and must not be hidden by empirical calibration in the geometry routine.
    assert abs(a["A_air_total_m2"] - 46.49) / 46.49 < 0.02


def test_wavy_does_not_change_geometric_external_area():
    base = dict(
        face_width_m=1.1, face_height_m=0.5588, rows=4,
        transverse_pitch_m=0.0254, longitudinal_pitch_m=0.022,
        tube_od_m=0.00953, tube_thickness_m=0.00035,
        fpi=12.0, fin_thickness_m=0.00012,
    )
    p = geometry_areas(CoilGeometry(**base, fin_type="Plain fin"))
    w = geometry_areas(CoilGeometry(**base, fin_type="Wavy + louvers", wave_amplitude_2x_m=0.002, wave_half_period_m=0.001))
    assert math.isclose(p["A_air_total_m2"], w["A_air_total_m2"], rel_tol=0, abs_tol=1e-12)
