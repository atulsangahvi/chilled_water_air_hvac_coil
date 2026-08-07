# Physical Water Circuiting Guide - v2.4

## 1. What one dot means

The circuit editor is a cross-section through the coil.

- Horizontal map direction = rows through airflow depth (`R1`, `R2`, ...).
- Vertical map direction = tube positions from top to bottom (`T1`, `T2`, ...).
- Each dot = one complete straight tube pass running along the face width.

So `R6-T1` is the top tube in Row 6. A route such as `R6-T1 -> R5-T1 -> R4-T2 -> R3-T2` is the ordered coolant path. Every transition is a return bend/crossover at a tube end.

## 2. Even/odd outlet-end rule

A circuit reverses direction after every straight pass. Therefore:

- supply and return on the **same tube end** normally require an **even** pass count for every circuit;
- supply and return on **opposite tube ends** normally require an **odd** pass count for every circuit.

This rule applies independently to every parallel circuit.

## 3. Unequal circuit lengths are allowed

Equal pass counts are preferred because they reduce hydraulic and temperature maldistribution, but real coil geometry does not always divide exactly. v2.4 therefore permits intentional unequal circuit lengths and solves each route individually.

The important detail is outlet-end parity. With same-end headers, a 16-pass circuit and an 18-pass circuit are both valid because both are even. A 16-pass and 17-pass mixture would put the 17-pass outlet at the opposite tube end unless a special crossover/header arrangement is provided. With opposite-end headers, all routes must similarly remain odd-pass.

If no all-tubes-used distribution can satisfy that parity, practical manufacturing options include changing the circuit count, using a special crossover/Z arrangement, or leaving a tube blank/dropped. The current automatic generator uses all tubes only when the selected common outlet-end arrangement is physically routeable.

## 4. Three ways to create the circuit map

### Click-to-route
Choose the active circuit and click dots in exact coolant-flow order. A tube cannot belong to two circuits.

### Numerical route
Enter `R#-T#` IDs directly, for example `R6-T1 -> R5-T1 -> R4-T2 -> R3-T2`.

### Auto serpentine
The generator creates a compact nearest-neighbour starting arrangement. If identical routes are impossible but a parity-compatible unequal distribution exists, it deliberately uses the required shorter/longer pass counts and spreads the longer circuits over the face.

## 5. Circuit-resolved hydraulics

After a complete valid map is available, the solver uses actual pass count, tube length, return bends, branch positions and header progression for each circuit. It iterates parallel circuit flows toward common path pressure drop. Outputs include circuit mass flow, flow deviation, tube velocity, Reynolds number, core/bend/header pressure loss, total path pressure loss and maximum flow imbalance.

This is why an unequal route is not automatically rejected: its real consequence is calculated.

## 6. Fully coupled tube-by-tube thermal solve

v2.4 does **not** assign one common coolant temperature to every tube in a row. Each `R#-T#` cell has its own local state.

For each tube cell:

1. entering air comes from the previous row in the same vertical air lane;
2. entering coolant comes from the previous tube in that exact circuit;
3. the cell uses that circuit's resolved mass flow;
4. local cross-flow dry/part-wet/wet heat transfer is solved;
5. leaving air is passed to the next row in that lane;
6. leaving coolant is passed to the next tube in the circuit.

Because coolant and air paths cross, the full grid is iterated until local coolant inlet temperatures and air states converge. The row-by-row output is then a mixed/aggregated view of these individual cell solutions.

## 7. Remaining model assumptions

The v2.4 2-D model assumes equal entering dry-air mass flow among the vertical lanes and does not yet model lateral air redistribution or cross-fin conduction between adjacent tubes. These are explicit later refinements and should be assessed during validation against actual coil tests.
