# Engineering calculation notes

## 1. Why the DX formulas were not reused

A chilled-water coil has single-phase liquid inside the tubes. Refrigerant quality, boiling correlations, superheat zones, void fraction and two-phase refrigerant pressure-drop equations from the DX evaporator model are therefore removed.

## 2. Geometry convention

The app uses the conventional fin-tube nomenclature:

- `Pt`: transverse pitch, perpendicular to air flow (vertical tube center spacing)
- `Pl`: longitudinal pitch, row-to-row in the direction of air flow
- tube length = coil face width
- tubes per row = floor(face height / Pt)
- fin count = floor(tube length / fin pitch)
- total tubes = tubes per row × rows

This corrects a directional inconsistency in the uploaded DX code.

## 3. Air-side heat transfer and dry pressure drop

The default is the Wang–Tsai–Lu wavy/louvered fin correlation as documented in the ACHP fin-tube heat exchanger model. It calculates Colburn `j`, air-side `h`, Fanning friction factor and core pressure drop from Reynolds number, fin pitch, row count and area ratios.

The published experimental work reports Reynolds number based on tube/collar diameter in roughly the `3e2` to `8e3` range. The app warns when extrapolating.

Air-side correlations for fin-tube coils are empirical and geometry-specific. There is no single universal “latest” equation that is more correct for all fin tooling. For manufacturing use, the best model is a literature correlation matched to the actual fin family and then validated to test data.

## 4. Wet coil treatment

Condensation begins when the estimated surface temperature falls below the entering-air dew point. The app uses an enthalpy-potential wet/dry method with saturated-air enthalpy slope `c_s = d(h_sat)/dT` and a wet fin-efficiency correction. This is consistent in concept with established detailed chilled-water coil models used in EnergyPlus and ACHP dry/wet segment calculations.

Wet **air pressure drop** is not universal. Condensate retention, fin spacing, fin angle, coating, face velocity and drainage alter friction. The app therefore exposes a `wet/dry air ΔP ratio` as a calibration input and applies it only to the calculated wet fraction.

## 5. Water-side heat transfer

For smooth round tubes:

- Laminar baseline: `Nu = 3.66`
- Turbulent: Gnielinski form
- Transition 2300–3000: interpolation to avoid a discontinuity
- Properties: CoolProp at mean coolant temperature

Gnielinski remains a standard modern engineering correlation for turbulent internal flow; a newer publication date alone would not make another equation more accurate for ordinary smooth copper tubes.

## 6. Water pressure drop and circuiting

Each parallel circuit receives `total mass flow / number of circuits` under the equal-flow assumption. Integer tube counts are distributed among circuits. For every circuit the app includes:

- straight tube Darcy friction
- one configurable return-bend `K` for each tube-to-tube turn
- circuit takeoff/return `K`
- distributed supply header friction
- distributed return header friction
- common inlet and outlet `K`

Inlet and outlet headers each have independent OD and wall-thickness inputs; ID is calculated from them.

The app reports min/average/max circuit path pressure drop. A large spread is a warning that equal circuit flow may not be self-consistent and that a full hydraulic network solution or balancing modification is needed.

## 7. Standards / references to validate against

- AHRI Standard 410-2023 — Performance Rating of Forced-Circulation Air-Cooling and Air-Heating Coils.
- ASHRAE Handbook—Fundamentals 2025 — psychrometrics, heat transfer, fluid flow and secondary coolant properties.
- EnergyPlus current Engineering Reference — `Coil:Cooling:Water:DetailedGeometry`; based on Elmahdy & Mitalas detailed cooling/dehumidifying coil method.
- Wang, C.-C., Tsai, Y.-M., Lu, D.-C. (1998), comprehensive convex-louver/wavy fin-and-tube heat exchanger study.
- Wang et al. (1999), wet herringbone fin-tube air-side performance; shows condensate retention materially affects friction/heat transfer.
- Hong & Webb (1996), wet/dry fin efficiency.

## 8. Recommended validation before production release

Use 10–30 trusted coil operating points spanning rows, FPI, face velocity, entering wet-bulb, water flow and circuit count. Compare:

1. total capacity
2. sensible capacity / SHR
3. leaving DB/WB
4. leaving water temperature
5. air pressure drop
6. water pressure drop

Calibrate only physically defensible multipliers/K values, then lock them by fin tooling and header/bend construction rather than fitting each coil independently.
