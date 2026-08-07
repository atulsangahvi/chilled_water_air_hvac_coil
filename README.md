# Chilled Water Cooling Coil Designer

Streamlit engineering app for **wet/dry chilled-water fin-and-tube cooling coils**. It was rebuilt from a DX evaporator app geometry/UI concept, but removes DX refrigerant evaporation/superheat calculations and replaces them with single-phase water/glycol heat transfer and hydraulics.

## Main features

- Wet, partially wet and dry coil thermal performance
- Leaving air DB/WB/RH, total/sensible/latent capacity, SHR and condensate
- Water, ethylene glycol and propylene glycol via CoolProp
- Air-side heat transfer and dry pressure drop using a Wang–Tsai–Lu wavy/louvered fin correlation
- Water-side Gnielinski heat transfer coefficient
- Darcy–Weisbach tube pressure loss with Churchill friction
- Parallel circuit model with integer tube counts and return-bend K losses
- Separate **inlet and outlet header OD + wall thickness**
- Distributed header pressure drop for each circuit path
- Same-end vs opposite-end header arrangement
- Min/average/max circuit-path water pressure drop
- Air HTC, air pressure-drop and wet pressure-drop calibration factors
- Multi-user login: `admin`, `engineer1`, `engineer2` (or any users you configure)
- JSON, CSV and PDF outputs

## Important geometry correction versus the uploaded DX app

For a conventional horizontal-tube coil:

- tube length = face width
- tubes per row = face height / transverse (vertical) tube pitch
- fin count = tube length / fin pitch

The earlier DX code mixed some of these directions, which affects heat-transfer area and free-flow area.

## GitHub / Streamlit deployment

1. Put all files in a GitHub repository.
2. Do **not** commit `.streamlit/secrets.toml`.
3. Install locally:

```bash
pip install -r requirements.txt
```

4. Create the three local user passwords in one step:

```bash
python setup_users.py
```

   Alternatively, use `generate_password_hash.py` and fill `.streamlit/secrets.toml.example` manually.

5. Run locally:

```bash
streamlit run app.py
```

### Streamlit Community Cloud secrets

In the Streamlit app settings, open **Secrets** and paste the contents in the same TOML structure as `.streamlit/secrets.toml.example`. The real secret file should never be stored in GitHub.

## Engineering basis and limitations

This is an engineering correlation model, not an AHRI-certified selection program.

- **Rating framework:** use AHRI 410-2023 as the current forced-circulation air-cooling/heating coil rating reference.
- **Wet/dry method:** enthalpy-potential / dry-wet surface treatment consistent in concept with established detailed chilled-water coil methods (EnergyPlus/Elmahdy-Mitalas and ACHP dry-wet segment approaches).
- **Air side:** Wang–Tsai–Lu wavy/louvered fin j/f correlation. It is empirical and should only be used near its tested geometry/Reynolds range.
- **Water side:** Gnielinski is an established modern turbulent internal-flow correlation; the app transitions to laminar behavior at low Reynolds number.
- **Wet air pressure drop:** condensate retention is strongly geometry/drainage dependent. The app exposes a wet/dry ΔP ratio instead of hiding an unsupported fixed correction.
- **Headers and return bends:** fitting K values are configurable because actual bend radius, tee geometry and brazed header takeoffs matter.

Before manufacturing release, calibrate against several trusted coil selections or preferably your own calorimeter/wind-tunnel test data (capacity, leaving conditions, air ΔP and water ΔP).

## Suggested next validation phase

Build a validation CSV containing manufacturer/test points with geometry, airflow, EDB/EWB or RH, entering/leaving water temperature, water flow, total capacity, air ΔP and water ΔP. Then fit/verify only the permitted calibration multipliers and document error bands. Avoid fitting every point independently.

## v2.0 — Segmented chilled-water row marching

Version 2 adds the requested design/check workflow:

- Entering air can be entered as **DB + RH** or **DB + WB**.
- Airflow can be entered as **face velocity (m/s)**, **m³/s**, **m³/h**, or **CFM**.
- Design target can be either:
  - required total cooling capacity in **kW**, or
  - required leaving-air condition using **DB + RH** or **DB + WB**.
- Enter coolant type, glycol concentration, entering coolant temperature, **absolute pressure**, flow rate, circuits, tube geometry, and supply/return header OD + wall thickness.
- The thermal solver marches **air row-by-row**. With the counterflow option, the opposing chilled-water row temperatures are iterated to convergence.
- Results include row entering/leaving air DB/WB/RH/humidity ratio, row entering/leaving coolant temperature, row kW, wet fraction, and row heat-capacity-rate diagnostics.
- Main results include:
  - total/sensible/latent kW and SHR,
  - leaving air DB/WB/RH and leaving coolant temperature,
  - air-side and coolant-side pressure drops,
  - face velocity and maximum air velocity through the minimum free-flow area,
  - velocity inside one tube and supply/return header velocities,
  - Reynolds and Prandtl numbers on both sides,
  - air and coolant heat-capacity rates `C = m_dot * cp`, `Cmin`, `Cmax`, `Cr`, NTU and effectiveness,
  - capacity-rate limiting side and thermal-resistance limiting side,
  - automatic one-variable design alternatives: add rows, increase coolant flow, or increase face area.

### Important circuiting note

The v2 row solver is an **equivalent row-bank model**. It assumes the coolant progresses row-to-row according to the selected counterflow or parallel-flow arrangement. This is suitable for design iteration and row temperature diagnostics, but **exact tube-by-tube water temperatures require the physical circuit map** (tube sequence, return bends, and header branch connection for each circuit). That can be added as the next circuiting module.
