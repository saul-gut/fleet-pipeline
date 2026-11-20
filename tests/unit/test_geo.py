"""La distancia geodésica de Python debe coincidir con PostGIS y con valores conocidos."""

import pytest

from fleet_pipeline.geo import geodesic_distance_m

PLAZA_BOLIVAR = (4.59808, -74.07609)
PARQUE_BERRIO = (6.25184, -75.56359)


def test_one_degree_of_latitude_at_equator_matches_wgs84_reference() -> None:
    # Arco de meridiano entre 0° y 1° en WGS84: 110 574,389 m (también lo devuelve PostGIS).
    assert geodesic_distance_m(0, 0, 1, 0) == pytest.approx(110_574.389, abs=0.001)


def test_bogota_medellin_matches_postgis_geography() -> None:
    # Valor de referencia: ST_Distance(::geography) en PostGIS 3.5 = 246 209,157 m.
    distance = geodesic_distance_m(*PLAZA_BOLIVAR, *PARQUE_BERRIO)
    assert distance == pytest.approx(246_209.157, abs=0.001)


def test_distance_is_symmetric_and_zero_for_same_point() -> None:
    forward = geodesic_distance_m(*PLAZA_BOLIVAR, *PARQUE_BERRIO)
    backward = geodesic_distance_m(*PARQUE_BERRIO, *PLAZA_BOLIVAR)
    assert forward == pytest.approx(backward)
    assert geodesic_distance_m(*PLAZA_BOLIVAR, *PLAZA_BOLIVAR) == 0
