import math

from coil_core import (
    AirCondition, CoilGeometry, HydraulicInputs, geometry_areas,
    segmented_thermal_performance, suggested_coolant_flow_for_velocity,
)
from condenser_geometry import coil_geometry
from reporting import build_pdf, build_output_pdf


def geometry(**kw):
    return CoilGeometry(1.2, .85, 3, .0254, .022, .00953, .00035,
                        10., .00012, **kw)


def test_round_plate_fin_is_identical_to_condenser_geometry():
    g=geometry()
    a=geometry_areas(g)
    baseline=coil_geometry(1.2,.85,3,.022,.0254,.00953,.00953,.00012,10.)
    assert a['n_fins']==baseline['fin_count']
    assert a['n_tubes_per_row']==baseline['tubes_per_row']
    assert a['n_tubes_total']==baseline['total_tubes']
    assert math.isclose(a['A_fin_m2'],baseline['fin_total_net_area_m2'])
    assert math.isclose(a['A_bare_m2'],baseline['tube_exposed_outside_area_m2'])


def test_flat_plate_and_serpentine_area_and_internal_water_flow():
    plate=geometry(tube_minor_axis_m=.006)
    serpent=geometry(tube_minor_axis_m=.006,fin_construction='Serpentine fins',fin_type='Wavy fin')
    for g in (plate,serpent):
        a=geometry_areas(g)
        shared=coil_geometry(1.2,.85,3,.022,.0254,.00953,.006,.00012,10.,g.fin_construction)
        assert a['n_fins']==shared['fin_count']
        assert math.isclose(a['A_fin_m2'],shared['fin_total_net_area_m2'])
        assert math.isclose(a['A_bare_m2'],shared['tube_exposed_outside_area_m2'])
        assert math.isclose(a['free_flow_area_m2'],shared['minimum_free_flow_area_m2'])
        assert math.isclose(a['inside_flow_area_m2'],math.pi*(.00953-.0007)*(.006-.0007)/4)
        flow=suggested_coolant_flow_for_velocity(.00953,.00035,10,1.0,998.,.006)
        assert math.isclose(flow['tube_flow_area_m2'],a['inside_flow_area_m2'])
    assert geometry_areas(serpent)['serpentine_strip_count']==32
    assert geometry_areas(serpent)['A_bare_m2']>geometry_areas(plate)['A_bare_m2']


def test_flat_serpentine_thermal_march_and_both_pdf_reports():
    g=CoilGeometry(.2,.10,2,.025,.022,.00953,.0003,8,.00012,
                   fin_type='Wavy fin',tube_minor_axis_m=.006,fin_construction='Serpentine fins')
    h=HydraulicInputs(2,.22,.028,.001,.028,.001,.10)
    r=segmented_thermal_performance(g,AirCondition(27,50),.035,'Water',0,7,300000,h)
    assert math.isfinite(r['Q_total_kW']) and r['Q_total_kW']>0
    assert len(r['row_table'])==2
    water_kw=.22*r['water_props']['cp']*(r['water_out_C']-7)/1000
    assert abs(r['Q_total_kW']-water_kw)/r['Q_total_kW']<.02
    inp={'face_width_m':.2,'face_height_m':.10,'rows':2,'Pt_mm':25,'Pl_mm':22,
         'tube_OD_mm':9.53,'tube_minor_axis_mm':6,'tube_wall_mm':.3,
         'tube_shape':'Flat / elliptical tube','fin_construction':'Serpentine fins',
         'FPI':8.,'fin_pitch_mm':25.4/8,'fin_thickness_mm':.12,
         'fin_material':'Aluminum','tube_material':'Copper','fin_type':'Wavy fin',
         'airflow_m3_s':.035,'airflow_m3_h':126.,'airflow_CFM':74.,
         'air_in_DB_C':27.,'air_in_RH_pct':50.,'coolant':'Water',
         'glycol_pct':0.,'water_in_C':7.,'water_pressure_kPa_abs':300,
         'water_mdot_kg_s':.22,'water_volume_m3_h':.8,'circuits':2}
    assert build_output_pdf(inp,r,None,'test').startswith(b'%PDF-')
    assert build_pdf(inp,r,None,[], 'test').startswith(b'%PDF-')
