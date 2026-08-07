# Chilled Water Cooling Coil Designer

Streamlit engineering app for **wet/dry chilled-water fin-and-tube cooling coils**. It was rebuilt from a DX evaporator app geometry/UI concept, but removes DX refrigerant evaporation/superheat calculations and replaces them with single-phase water/glycol heat transfer and hydraulics.

### Explicit tube-row input
The Design Inputs page includes **Number of tube rows in coil (airflow direction)**. The selected row count is used directly in coil depth, heat-transfer area, air pressure drop, water circuit length and the row-by-row thermal marching model. The UI also displays nominal tube-bank depth = rows x row pitch.


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


## Streamlit Cloud login setup (important)

The app supports two credential formats in **Streamlit Cloud Secrets**. The easiest is:

```toml
[auth.users.admin]
role = "admin"
password = "YOUR_ADMIN_PASSWORD"

[auth.users.engineer1]
role = "engineer"
password = "YOUR_ENGINEER1_PASSWORD"

[auth.users.engineer2]
role = "engineer"
password = "YOUR_ENGINEER2_PASSWORD"
```

Do **not** put those real passwords in GitHub. In Streamlit Cloud open the deployed app, go to **Settings → Secrets**, paste the TOML above with your own passwords, save, and reboot/redeploy the app.

Alternatively, run `python setup_users.py` locally and copy the generated bcrypt hashes from `.streamlit/secrets.toml` into Streamlit Cloud Secrets. Never use the placeholder `$2b$12$REPLACE...` text as a password hash; it is not a valid bcrypt hash and older versions of the app would raise `ValueError: Invalid salt`.

## v2.2 - cross-flow terminology, fin options, responsive UI, PDF fix

This revision responds to the coil-construction and UI review of 7 August 2026.

### Fin/tube inputs

- **FPI is a direct visible manufacturing input** in the Fin and Tube Construction section.
  The app also displays `fin pitch = 25.4/FPI` in mm.
- Fin materials: **Aluminum, Copper, Steel**.
- Tube materials: **Copper, Aluminum, Steel, CuNi 90/10**.
- Fin families shown to the user are exactly:
  - **Plain fin**
  - **Wavy fin**
  - **Wavy + louvers**

Correlation policy:

- Plain fin -> Wang, Chi & Chang (2000) plain fin-and-tube `j/f` correlation.
- Wavy + louvers -> Wang-Tsai-Lu correlation as documented by ACHP.
- Wavy fin -> a clearly-labelled engineering baseline using the verified plain-fin Wang
  correlation with developed wavy surface area.  It is intentionally **not** presented as
  the dedicated Wang-Jang-Chiou (1999) equation because the full verified coefficient set is
  not reproduced in the open references bundled with this repository.  Calibrate this mode
  against the actual fin die/test data before production use.

### Physical cross-flow clarification

The actual fin-and-tube HVAC coil is modeled as **CROSS-FLOW** at every row:

- air moves through coil depth;
- coolant moves along the tubes;
- the local directions are approximately 90 degrees.

The previous short labels `Parallel flow` / `Counterflow` were misleading.  The UI now uses:

- **Water enters air-leaving side (cross-counterflow tendency)**
- **Water enters air-entering side (cross-parallelflow tendency)**

These choices only define the **coolant row progression through coil depth**.  They do not
change the local air/tube geometry from cross-flow.  Each dry row uses a cross-flow
`epsilon-NTU` relation.

### Responsive results screen

The wide six-metric rows were removed.  Results now use a maximum of three metric cards per
row, and long results such as the wet-surface state and limiting-side explanations are shown
in full-width information blocks.  Row detail has a compact default table plus an expandable
full diagnostic table.

### PDF revision

The v2 report was reviewed and the v2.3 circuiting appendix was added.  The old report allowed the velocity table to start on page 1 and
continue on page 2, and it described the row arrangement simply as `Counterflow`.  v2.2:

- keeps each major heading with its table;
- keeps the complete velocity table together;
- adds an explicit design-input/construction table including FPI, fin pitch, fin type and
  materials;
- states **Physical flow geometry: CROSS-FLOW**;
- describes only the water **row progression** as cross-counterflow/cross-parallelflow;
- places the row-by-row thermal-marching table on a **landscape A4 page** so readable fonts
  can be retained.

The PDF generator includes dedicated landscape pages for physical circuiting and tube-by-tube thermal results when a complete route is available.

## v2.3 - Physical circuiting editor

v2.3 adds a manufacturing-oriented water-circuit editor.

### Tube naming

The circuit cross-section uses:

- `R1 ... RN`: tube rows in the **airflow direction**, from entering-air face to leaving-air face.
- `T1 ... TM`: tube positions from top to bottom of the coil face.
- Example route: `R6-T1 -> R5-T1 -> R4-T2 -> R3-T2`.

Each listed tube is one straight tube pass along the coil face width. Each transition between two tube IDs is a return bend at a tube end.

### Automatic manufacturing compatibility checks

The app calculates:

`total tubes = tubes per row x number of rows`

and checks the selected number of parallel circuits. A fully balanced circuit bank has an integer and equal number of tube passes per circuit. The circuit editor also checks tube-end parity:

- **Supply and return at the same tube end:** normally an **even** number of straight passes per circuit.
- **Supply and return at opposite tube ends:** normally an **odd** number of straight passes per circuit.

Nearby balanced circuit counts are suggested automatically if the selected count is incompatible.

### Visual and numerical circuit creation

The `Circuiting` tab provides three ways to make circuits:

1. **Click-to-route matrix** - select the active circuit and click tube dots in water-flow sequence.
2. **Numerical route entry** - type `R#-T#` tube IDs separated by arrows, commas, semicolons, or new lines.
3. **Auto-generate serpentine** - create a balanced nearest-neighbour starting circuit map when the tube/circuit combination is compatible.

The app prevents a tube from belonging to two circuits, identifies unassigned tubes, checks unequal pass counts, and warns about unusually long return-bend centre spacing.

### Circuit-resolved hydraulics

When every tube is assigned and the route is valid, the analysis changes from the earlier equal-flow circuit-count diagnostic to an explicit routed-circuit network. It uses:

- actual number of straight passes in each circuit;
- actual number of return bends;
- vertical location of each circuit supply branch and return branch;
- supply-header feed end;
- same-end or opposite-end return-header outlet arrangement;
- iterative circuit-flow balancing so parallel path pressure drops approach equality.

Outputs include circuit mass flow, deviation from equal flow, tube velocity, Reynolds number, core/bend/branch pressure drop, supply-header loss, return-header loss, total circuit-path loss, and maximum flow imbalance.

### Tube-by-tube thermal model

The v2.3 row-duty-conserving coolant-temperature post-processor has been superseded by the v2.4 fully coupled 2-D solver described below. The physical circuit map is still used for the hydraulic network and PDF circuit drawing.

The PDF report includes the physical circuit map, circuit hydraulic table, circuit outlet temperatures, and tube-by-tube thermal results when a complete circuit route is defined.

## v2.4 - Unequal circuits and fully coupled tube-by-tube / air-lane solve

Real coil blocks do not always divide into identical circuit lengths. v2.4 therefore distinguishes **preferred equal circuits** from **acceptable unequal circuits**. Unequal routes are allowed when each route exits at the intended header end. With same-end supply/return this normally means every route remains even-pass; with opposite-end connections every route remains odd-pass. Consequently, unequal routes commonly differ by two straight passes (for example 16 and 18), not by one. If the total tube count cannot satisfy the required parity, manufacturing practice may require a blank/dropped tube or a special crossover/header arrangement.

When the selected geometry can use all tubes with the required outlet-end parity, the automatic circuit generator deliberately distributes the longer routes across the face. The explicit hydraulic network then solves the parallel flows toward equal path pressure loss instead of forcing equal mass flow.

With a complete physical circuit map, the thermal solver is no longer a row-average post-processor. The coil is discretized as `R#-T#` cells. For every cell the solver uses:

- local entering air state from the previous row in that vertical air lane;
- local entering coolant temperature from the previous tube in that circuit;
- the individually solved circuit mass flow;
- local cross-flow dry/part-wet/wet heat transfer;
- local tube-side Reynolds/Prandtl/HTC and air-side state/properties.

The leaving air from each cell becomes the entering air to the next row in that lane, while the leaving coolant becomes the entering coolant to the next tube in the routed circuit. The entire air/coolant grid is iterated to convergence. The familiar row-by-row table remains, but it is now an **aggregate of the individual tube/air-lane solutions**, not the state used to solve every tube in a row.

Remaining explicit refinements are lateral air redistribution among vertical lanes and cross-fin conduction between adjacent tube cells. Those effects should be considered during later validation against actual coil test data.
