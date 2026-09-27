import pytest

from circuit_picker import apply_tube_click
from circuit_plot import selected_tube, tube_plot
from circuiting import circuit_svg,validate_routes


def test_clicks_build_complete_ordered_routes_and_remain_editable():
    routes={1:[],2:[]}
    for label in ('R2-T1','R1-T1','R2-T2','R1-T2'):
        apply_tube_click(routes,1,label,2,4)
    for label in ('R2-T3','R1-T3','R2-T4','R1-T4'):
        apply_tube_click(routes,2,label,2,4)
    check=validate_routes(routes,2,4,2,'Same tube end (even passes/circuit required)',.025,.022)
    assert check['valid'] and check['complete']
    svg=circuit_svg(2,4,routes)
    assert svg.count('data-tube=')==8
    assert 'data-tube="R2-T1"' in svg
    assert routes[1][0]=='R2-T1' and routes[1][-1]=='R1-T2'
    apply_tube_click(routes,1,'R1-T2',2,4)
    assert routes[1]==['R2-T1','R1-T1','R2-T2']
    assert not validate_routes(routes,2,4,2,'Same tube end (even passes/circuit required)')['complete']


def test_click_rejects_stealing_outside_geometry_and_non_last_removal():
    routes={1:['R2-T1','R1-T1'],2:[]}
    for tube,circuit in [('R2-T1',2),('R9-T1',1),('R2-T1',1)]:
        with pytest.raises(ValueError):
            apply_tube_click(routes,circuit,tube,2,2)
    assert routes=={1:['R2-T1','R1-T1'],2:[]}


def test_plot_selection_identifies_only_tube_markers():
    fig=tube_plot(2,4,{1:['R2-T1','R1-T1'],2:[]})
    marker_index=len(fig.data)-1
    marker=fig.data[marker_index]
    assert len(marker.customdata)==8
    assert marker.customdata[1][0]=='R2-T1'
    selection={'points':[{'curve_number':marker_index,'customdata':['R2-T1']}]}
    assert selected_tube(selection,marker_index)=='R2-T1'
    assert selected_tube({'points':[{'curve_number':0,'customdata':['R2-T1']}]},marker_index) is None
