from hydraulic_margin import coolant_pressure_margin


def test_largest_circuit_loss_controls_static_margin():
    good = coolant_pressure_margin(300, 55, 7, 12)
    poor = coolant_pressure_margin(25, 55, 7, 12)
    assert good["minimum_outlet_kPa_abs"] == 245
    assert good["margin_over_water_vapor_kPa"] > 0
    assert poor["margin_over_water_vapor_kPa"] < 0
    assert poor["status"] == "CHECK SUPPLY PRESSURE"
