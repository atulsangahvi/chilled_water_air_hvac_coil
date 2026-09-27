"""Click a tube on the SVG circuit view and append it to an ordered route."""
from __future__ import annotations

from circuiting import parse_tube_id, tube_id


def apply_tube_click(routes, active_circuit: int, clicked: str,
                     rows: int, tubes_per_row: int) -> str:
    """Mutate the selected route once; reject duplicates and out-of-bank labels."""
    r,t=parse_tube_id(clicked)
    if not (1<=r<=int(rows) and 1<=t<=int(tubes_per_row)):
        raise ValueError('Clicked tube is outside the selected coil geometry.')
    label=tube_id(r,t)
    active=int(active_circuit)
    if active not in routes:
        raise ValueError('Select an existing water circuit first.')
    owner=next((cid for cid,route in routes.items() if label in route),None)
    if owner is None:
        routes[active].append(label)
        return f'Added {label} as pass {len(routes[active])} in Circuit {active}.'
    if owner==active and routes[active][-1]==label:
        routes[active].pop()
        return f'Removed the last pass, {label}, from Circuit {active}.'
    if owner==active:
        raise ValueError(f'{label} is an earlier pass in Circuit {active}. Use Undo last tube to keep its order.')
    raise ValueError(f'{label} already belongs to Circuit {owner}. Clear or edit that circuit first.')


_JS = r"""
export default function({parentElement, data, setTriggerValue}) {
  const host = parentElement.querySelector('.cw-circuit-host');
  host.innerHTML = data.svg;
  host.querySelectorAll('circle[data-tube]').forEach(dot => {
    const activate = () => setTriggerValue('clicked', dot.dataset.tube);
    dot.addEventListener('click', activate);
    dot.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); activate();
      }
    });
  });
}
"""

_CSS = r"""
.cw-circuit-host {width:100%; overflow-x:auto}
.cw-circuit-host svg {min-width:560px; max-width:100%; height:auto}
.cw-circuit-host circle[data-tube] {cursor:pointer; transition:stroke-width .12s, r .12s}
.cw-circuit-host circle[data-tube]:hover,
.cw-circuit-host circle[data-tube]:focus {stroke-width:4; outline:2px solid #2563eb}
"""


def register_clickable_svg():
    """Register the official Streamlit v2 component once per Python process."""
    import streamlit as st
    if not hasattr(st.components,'v2'):
        return None
    return st.components.v2.component(
        'cw_clickable_circuit_svg',html='<div class="cw-circuit-host"></div>',
        css=_CSS,js=_JS,
    )
