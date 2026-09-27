"""Plate-fin coil geometry with the v28.7 air-cooled condenser orientation.

Tube axis = coil face width. Tubes per row count along face height at vertical
pitch. Plate fins repeat along the tube axis and span face height x row depth.
Areas are geometric (before fin efficiency and fouling).
"""
from __future__ import annotations

import math


def ellipse_perimeter(major: float, minor: float) -> float:
    """Ramanujan's perimeter approximation for full outside axes."""
    a, b = major / 2, minor / 2
    h = ((a - b) / (a + b)) ** 2
    return math.pi * (a + b) * (1 + 3*h/(10 + math.sqrt(4 - 3*h)))


def coil_geometry(face_width_m, face_height_m, rows, row_pitch_m,
                  vertical_pitch_m, tube_major_m, tube_minor_m,
                  fin_thickness_m, fpi, fin_construction='Plate fins'):
    """Consistent tube/fin orientation for round and airflow-aligned elliptical tubes.

    FPI counts plates on tube length for plate fins, inclined legs on tube length
    for serpentine fins. Each serpentine strip occupies one vertical tube gap.
    """
    vals = (face_width_m,face_height_m,row_pitch_m,vertical_pitch_m,
            tube_major_m,tube_minor_m,fin_thickness_m,fpi)
    if any(not math.isfinite(float(v)) or v <= 0 for v in vals) or int(rows)!=rows or rows<1:
        raise ValueError('All geometry dimensions must be positive and finite; rows must be a positive integer')
    if tube_minor_m > tube_major_m:
        raise ValueError('Tube minor outside axis must not exceed major axis in airflow direction')
    if vertical_pitch_m <= tube_minor_m or row_pitch_m <= tube_major_m:
        raise ValueError('Vertical tube pitch must exceed minor axis and row pitch must exceed major axis')
    pitch = .0254 / fpi
    if pitch <= fin_thickness_m:
        raise ValueError('Fin pitch must exceed fin thickness')
    tubes_per_row = int(face_height_m / vertical_pitch_m)
    if tubes_per_row < 1:
        raise ValueError('Face height must fit at least one tube at the selected vertical pitch')
    n_tubes = tubes_per_row * rows
    depth = rows * row_pitch_m
    p_ext = ellipse_perimeter(tube_major_m,tube_minor_m)
    gross_tube = n_tubes * p_ext * face_width_m
    face_area = face_width_m * face_height_m
    tube_block = min(face_area,tubes_per_row * face_width_m * tube_minor_m)
    fin_count = max(1,int(round(face_width_m/pitch)))
    fin_gross = 0.0
    holes_area = 0.0
    serpentine_leg_length = 0.0
    if fin_construction == 'Plate fins':
        fin_gross = 2*face_height_m*depth
        holes_area = 2*n_tubes*math.pi*tube_major_m*tube_minor_m/4
        net_each = fin_gross-holes_area
        if net_each<=0:
            raise ValueError('Tube holes exceed plate-fin area')
        fin_area=fin_count*net_each
        contact_length=min(face_width_m,fin_count*fin_thickness_m)
        fin_block = min(face_area-tube_block,
                        contact_length*max(0.,face_height_m-tubes_per_row*tube_minor_m))
        strip_count=0
    elif fin_construction == 'Serpentine fins':
        strip_count=max(0,tubes_per_row-1)
        gap=vertical_pitch_m-tube_minor_m
        serpentine_leg_length=math.hypot(gap,pitch)
        # FPI is the number of inclined legs per inch along tube length.
        fin_area=2*strip_count*depth*fin_count*serpentine_leg_length
        net_each=2*depth*serpentine_leg_length
        contact_length=min(face_width_m,fin_count*fin_thickness_m)
        fin_block=min(face_area-tube_block,strip_count*fin_thickness_m*fin_count*serpentine_leg_length)
    else:
        raise ValueError('Fin construction must be Plate fins or Serpentine fins')
    # Plate-fin contact removes the full tube perimeter; serpentine contact is
    # approximated by two fin thicknesses at each leg/tube join.
    covered_tube=(n_tubes*p_ext*contact_length if fin_construction=='Plate fins' else 0.)
    bare_tube=gross_tube-covered_tube
    Amin=face_area-tube_block-fin_block
    if Amin<=0: raise ValueError('No free airflow area remains')
    return dict(face_width_m=face_width_m,face_height_m=face_height_m,tube_length_m=face_width_m,
        vertical_tube_pitch_m=vertical_pitch_m,row_pitch_m=row_pitch_m,rows=rows,coil_depth_m=depth,
        tube_major_axis_m=tube_major_m,tube_minor_axis_m=tube_minor_m,
        fin_construction=fin_construction,fin_pitch_m=pitch,fin_count=fin_count,
        serpentine_strip_count=strip_count,serpentine_leg_length_m=serpentine_leg_length,
        serpentine_full_wave_pitch_m=2*pitch if strip_count else 0.,
        serpentine_developed_length_per_strip_m=fin_count*serpentine_leg_length,
        tubes_per_row=tubes_per_row,total_tubes=n_tubes,total_straight_tube_length_m=n_tubes*face_width_m,
        fin_gross_area_per_plate_two_faces_m2=fin_gross,tube_hole_count_per_plate=n_tubes if strip_count==0 else 0,
        fin_hole_deduction_per_plate_two_faces_m2=holes_area,
        fin_net_area_per_plate_two_faces_m2=net_each,fin_total_net_area_m2=fin_area,
        tube_gross_outside_area_m2=gross_tube,tube_area_under_fin_thickness_m2=covered_tube,
        tube_exposed_outside_area_m2=bare_tube,total_air_side_area_m2=fin_area+bare_tube,
        face_area_m2=face_area,tube_projected_blockage_m2=tube_block,fin_edge_blockage_m2=fin_block,
        minimum_free_flow_area_m2=Amin,minimum_free_flow_area_ratio=Amin/face_area,
        orientation=('Tubes run along coil face width; major ellipse axis points into airflow depth. '
            +('Plates span face height x depth and repeat along tube length.' if strip_count==0 else
            'One serpentine strip fills each vertical tube gap; FPI counts inclined legs along tube length.')))


def plate_fin_geometry(face_width_m: float, face_height_m: float, rows: int,
                       row_pitch_m: float, vertical_pitch_m: float,
                       tube_od_m: float, fin_thickness_m: float, fpi: float) -> dict:
    return coil_geometry(face_width_m,face_height_m,rows,row_pitch_m,vertical_pitch_m,
                         tube_od_m,tube_od_m,fin_thickness_m,fpi,'Plate fins')
