"""Keep capacity-only selection separate from a specified leaving-air state."""


def assess_chilled_water_target(result, target, db_tolerance_C=.3,
                                humidity_tolerance_g_kg=.5):
    if db_tolerance_C < 0 or humidity_tolerance_g_kg < 0:
        raise ValueError('Target tolerances must be nonnegative.')
    q=float(result['Q_total_kW'])
    required=float(target['Q_required_kW'])
    capacity_met=q>=required-1e-6
    requested=target.get('target_air') or {}
    actual=result['air_out']
    air_specified=requested.get('T_C') is not None and requested.get('W') is not None
    db_delta=(actual['T_C']-requested['T_C']) if air_specified else None
    humidity_delta=(actual['W']-requested['W'])*1000 if air_specified else None
    db_limit_met=db_delta is not None and db_delta<=.05
    humidity_limit_met=humidity_delta is not None and humidity_delta<=.01
    exact_match=(air_specified and abs(db_delta)<=db_tolerance_C
                 and abs(humidity_delta)<=humidity_tolerance_g_kg)
    thermal_met=capacity_met and (exact_match if air_specified else True)
    margin=result.get('pressure_margin',{}).get('margin_over_water_vapor_kPa')
    pressure_ok=margin is not None and margin>0
    route_complete=str(result.get('circuit_model','')).startswith('Explicit routed circuits')
    if not capacity_met:
        status='CAPACITY TARGET NOT MET'
    elif air_specified and not exact_match:
        status='LEAVING-AIR DESIGN POINT NOT MATCHED'
    elif not pressure_ok:
        status='COOLANT PRESSURE SCREEN NOT PASSED'
    elif not route_complete:
        status='THERMAL TARGET MET; CIRCUIT ROUTE UNVERIFIED'
    elif not air_specified:
        status='CAPACITY-ONLY TARGET MET; AIR STATE NOT SPECIFIED'
    else:
        status='LEAVING-AIR DESIGN POINT MATCHED (SCREENING)'
    return dict(status=status,capacity_met=capacity_met,air_state_specified=air_specified,
                db_limit_met=db_limit_met,humidity_limit_met=humidity_limit_met,
                leaving_air_matched=exact_match,thermal_target_met=thermal_met,
                coolant_pressure_screen_passed=pressure_ok,physical_route_complete=route_complete,
                db_error_C=db_delta,humidity_error_g_kg=humidity_delta,
                db_tolerance_C=db_tolerance_C,
                humidity_tolerance_g_kg=humidity_tolerance_g_kg)
