# Formula Audit — Chilled-Water Fin-and-Tube Coil

Audit date: 7 August 2026

## Executive conclusion

The uploaded DX evaporator model cannot be converted by only replacing refrigerant properties. Its boiling, quality, superheat and two-phase pressure-drop equations are not applicable to chilled-water coils. The new app therefore uses a separate single-phase liquid model.

There is also no single universal "latest" air-side equation for every fin-and-tube coil. Air-side heat transfer and pressure drop are empirical and depend on the exact fin tooling, collar geometry, row pitch, tube pitch, FPI, surface treatment and condensate drainage. A current production-grade workflow is therefore:

1. use an established correlation that matches the actual fin family;
2. stay within its tested geometry/Reynolds range;
3. calibrate to coil test or trusted manufacturer data;
4. use AHRI 410-2023 procedures for rating/certification work.

## 1. Standards and current model check

### AHRI 410-2023
AHRI currently lists **AHRI 410-2023 — Performance Rating of Forced-Circulation Air-Cooling and Air-Heating Coils**. It covers round-tube forced-circulation air cooling/heating coils and includes water/glycol coil rating procedures. AHRI also publishes forms for air-side resistance, tube-side pressure drop, cooling/dehumidifying ratings and ethylene-glycol coils.

### EnergyPlus 25.1
The current EnergyPlus 25.1 Engineering Reference still describes `Coil:Cooling:Water:DetailedGeometry` using the established Elmahdy & Mitalas detailed wet/dry coil framework. This is useful evidence that older validated coil frameworks remain relevant; publication date alone is not a reason to replace them.

## 2. Geometry correction from the DX code

For the conventional coil orientation used by this app:

- tube length = face width;
- tubes per row = floor(face height / transverse tube pitch);
- fin count = floor(tube length / fin pitch);
- total tubes = tubes per row × rows.

This corrects the directional inconsistency in the DX app, which mixed face width/height when calculating tube and fin counts.

## 3. Air-side heat-transfer coefficient

Default: Wang–Tsai–Lu wavy/louvered fin correlation as documented by ACHP.

The app evaluates:

- free-flow area `Ac`;
- maximum air velocity through `Ac`;
- Reynolds number based on tube OD/collar diameter;
- Colburn `j` factor;
- `h_air = j * rho * u_max * cp / Pr^(2/3)`;
- fin efficiency and overall air-side surface efficiency.

This family is retained because it directly models the fin/tube geometry rather than using a generic duct Nusselt equation. The app warns when Reynolds number moves outside the approximate range of the underlying data.

**Production requirement:** lock one HTC calibration multiplier per actual fin die/tooling family after test validation; do not tune each coil independently.

## 4. Air-side pressure drop

Default dry-core pressure drop is calculated from the same Wang correlation family using its friction factor and minimum-area mass flux.

The literature and manufacturer experience show that wet-coil pressure drop is strongly affected by condensate retention, bridging, surface coating, FPI, face velocity and drainage. No universal wet multiplier is defensible for every coil. Therefore the app exposes:

- dry air-pressure-drop calibration multiplier;
- wet/dry pressure-drop ratio;
- calculated wet fraction.

The wet correction is applied only to the wetted fraction.

## 5. Water/glycol properties

CoolProp incompressible-fluid property models are used for:

- water;
- aqueous ethylene glycol (`INCOMP::MEG-x%`);
- aqueous propylene glycol (`INCOMP::MPG-x%`).

The current CoolProp 8.0 documentation lists both MEG and MPG as mass-based aqueous binary mixtures up to 60% concentration.

## 6. Water-side heat-transfer coefficient

For smooth round tubes:

- laminar baseline: `Nu = 3.66`;
- turbulent: Gnielinski correlation;
- transition: bounded interpolation between Reynolds 2300 and 3000;
- friction factor used by Gnielinski: Churchill all-Reynolds correlation;
- properties evaluated at mean coolant temperature.

Gnielinski is an established engineering choice for ordinary smooth-tube turbulent internal flow. A newer paper is not automatically more accurate for standard copper chilled-water tubes.

## 7. Tube circuit pressure drop

For every parallel circuit, the app calculates:

`ΔP_core = f (L/D) rho v²/2 + ΣK_bend rho v²/2`

where:

- each circuit gets total flow / number of circuits for the first equal-flow solution;
- integer tube counts are distributed between circuits;
- actual circuit tube length follows its tube count;
- every tube-to-tube return bend contributes a configurable `K`;
- circuit takeoff/return minor-loss `K` is configurable.

The app reports individual circuit path losses instead of only one averaged number.

## 8. Supply and return header pressure drop

Inlet/supply and outlet/return headers have separate inputs for:

- OD;
- wall thickness;
- calculated ID;
- roughness;
- length.

Header flow is reduced/accumulated segment-by-segment as branches leave or enter the header. The app supports same-end and opposite-end return arrangements and reports:

- supply-header maximum velocity;
- return-header maximum velocity;
- minimum circuit-path ΔP;
- average circuit-path ΔP;
- maximum circuit-path ΔP;
- pressure spread between paths.

A large path spread triggers a warning because the initial equal-flow assumption is then internally inconsistent. A later production phase can add a nonlinear hydraulic network solver to solve unequal circuit flows directly.

## 9. Wet/dry thermal calculation

The model first solves a dry coil and estimates surface temperatures. If the surface falls below the entering-air dew point, it switches to a wet/part-wet enthalpy-potential calculation using:

- saturated-air enthalpy at the coolant/surface temperature;
- `c_s = d(h_sat)/dT`;
- wet-fin efficiency correction based on `c_s/c_p`;
- iterative mean coolant temperature and coolant properties;
- energy balance for total load and leaving coolant temperature;
- psychrometric inversion for leaving DB/RH/WB and humidity ratio.

This is conceptually consistent with the established ACHP/EnergyPlus family of wet/dry coil methods. It is not a CFD or tube-by-tube circuit solver.

## 10. What still needs manufacturer validation

Before this software is used for guaranteed coil selections, validate at multiple operating points across the intended product range:

- total and sensible capacity;
- leaving DB/WB/RH;
- condensate rate;
- air pressure drop dry and wet;
- water pressure drop;
- water outlet temperature;
- low/high water velocity;
- 2/4/6/8-row coils;
- low/high FPI;
- low/high face velocity;
- multiple circuit counts and header sizes.

The most important empirical items to calibrate are air-side `h`, dry air ΔP, wet ΔP and actual return-bend/header branch-loss coefficients.

## References

- AHRI Standard 410-2023, *Performance Rating of Forced-Circulation Air-Cooling and Air-Heating Coils*.
- EnergyPlus 25.1 Engineering Reference, *Chilled-Water-Based Detailed Geometry Air Cooling Coil*.
- Wang, C.-C., Tsai, Y.-M., Lu, D.-C. (1998), *Comprehensive Study of Convex-Louver and Wavy Fin-and-Tube Heat Exchangers*.
- Hong, K. T., Webb, R. L. (1996), *Calculation of Fin Efficiency for Wet and Dry Fins*.
- Gnielinski turbulent internal-flow correlation; Churchill friction-factor correlation.
- CoolProp 8.0 incompressible-fluid documentation.
- ACHP fin-tube heat-exchanger and DryWetSegment documentation.

## v2 additions — August 2026

The current AHRI Forced-Circulation Air-Cooling and Air-Heating Coils certification operations manual (June 2026) requires participants to comply with **AHRI Standard 410-2023**. Certified data include capacity, air pressure drop, and water/aqueous glycol pressure drop including headers. The app therefore continues to report these as primary outputs, but it is not represented as certified selection software.

The segmented thermal solver now reports both heat-capacity-rate limitation and resistance limitation. This distinction is important:

- `C_air = m_dot_da * cp_moist_air`
- `C_coolant = m_dot_coolant * cp_coolant`
- `Cr = Cmin/Cmax`
- The stream with `Cmin` is the **capacity-rate limiting stream** for the dry-reference epsilon-NTU analysis.
- The side with the larger calculated share of `1/UA` is the **thermal-resistance limiting side**.

For wet rows, total heat transfer is evaluated with the enthalpy-potential method. A separate wet enthalpy effectiveness is reported because classic temperature effectiveness alone does not represent latent heat transfer.

The air velocity reported "inside the fins" is the maximum core velocity calculated from humid-air mass flow divided by the **minimum free-flow area** between fins/tubes. It is distinct from face velocity based on gross face area.
