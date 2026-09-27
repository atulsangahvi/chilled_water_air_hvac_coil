from target_assessment import assess_chilled_water_target


def _result(temperature,humidity,pressure=240,route=True):
    return {'Q_total_kW':34., 'air_out':{'T_C':temperature,'W':humidity},
            'pressure_margin':{'margin_over_water_vapor_kPa':pressure},
            'circuit_model':'Explicit routed circuits + fully coupled 2-D tube-by-tube thermal model'
                            if route else 'Equivalent row-bank model'}


def test_air_state_match_is_distinct_from_capacity_and_excess_dehumidification():
    target={'Q_required_kW':30.,'target_air':{'T_C':13.5,'W':.00892}}
    mismatch=assess_chilled_water_target(_result(14.03,.00801),target)
    assert mismatch['capacity_met']
    assert mismatch['humidity_limit_met']
    assert mismatch['status']=='LEAVING-AIR DESIGN POINT NOT MATCHED'
    matching=assess_chilled_water_target(_result(13.6,.00880),target)
    assert matching['status']=='LEAVING-AIR DESIGN POINT MATCHED (SCREENING)'


def test_capacity_only_cannot_be_reported_as_air_state_match():
    capacity=assess_chilled_water_target(_result(15,.009,route=False),
                                         {'Q_required_kW':30.,'target_air':None})
    assert capacity['capacity_met'] and not capacity['air_state_specified']
    assert capacity['status']=='THERMAL TARGET MET; CIRCUIT ROUTE UNVERIFIED'
    pressure=assess_chilled_water_target(_result(15,.009,pressure=-5),
                                         {'Q_required_kW':30.,'target_air':None})
    assert pressure['status']=='COOLANT PRESSURE SCREEN NOT PASSED'
