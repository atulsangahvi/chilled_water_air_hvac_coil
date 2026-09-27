"""Pressure availability check at the coil outlet, based on the routed path loss."""
from CoolProp.CoolProp import PropsSI


def coolant_pressure_margin(supply_kPa_abs, path_loss_kPa, inlet_C, outlet_C,
                            coolant="Water"):
    """Return a conservative static-pressure screen for single-phase coolant.

    The maximum path loss is used because the least favorable circuit may see the
    lowest outlet pressure. Pure-water vapor pressure is used as a screening
    reference even when glycol is selected; this is not a glycol mixture model.
    Static lift outside the coil and pump suction losses are not included.
    """
    outlet = float(supply_kPa_abs) - float(path_loss_kPa)
    hottest = max(float(inlet_C), float(outlet_C))
    vapor = PropsSI("P", "T", hottest + 273.15, "Q", 0, "Water") / 1000.0
    margin = outlet - vapor
    return {
        "supply_kPa_abs": float(supply_kPa_abs),
        "maximum_path_loss_kPa": float(path_loss_kPa),
        "minimum_outlet_kPa_abs": outlet,
        "pure_water_vapor_reference_kPa_abs": vapor,
        "margin_over_water_vapor_kPa": margin,
        "reference_temperature_C": hottest,
        "status": "CHECK SUPPLY PRESSURE" if margin <= 0 else "Positive static margin",
        "basis": "Pure-water saturation reference at warmest coil coolant; glycol mixtures require supplier vapor-pressure data.",
    }
