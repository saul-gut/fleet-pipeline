"""Lógica de geocercas sobre el dataset real (data/geofences/geofences.geojson), sin base de datos.

Shapely aplica la misma semántica DE-9IM que PostGIS: `within` es verdadero solo si el punto
está en el interior del polígono, nunca sobre su borde. Los tests de integración comprueban
que PostGIS responde lo mismo para los mismos puntos.
"""

import pytest
from shapely.geometry import MultiPolygon, Point, shape

from fleet_pipeline.geofences.loader import read_features
from fleet_pipeline.simulator.zones import ZoneIndex

FEATURES = read_features()
GEOMS = {f["properties"]["code"]: shape(f["geometry"]) for f in FEATURES}
LIMITS = {f["properties"]["code"]: f["properties"]["speed_limit_kmh"] for f in FEATURES}
SPECIAL_ZONES = [c for c in GEOMS if not c.endswith("-URB") and c != "COR-HWY"]

# Lugares de referencia (lat, lon), verificados contra las geocercas.
PLAZA_DE_BOLIVAR = (4.5981, -74.0761)  # centro de Bogotá, fuera de zonas especiales
VILLAVICENCIO = (4.1420, -73.6270)  # fuera de toda geocerca del dataset


@pytest.fixture(scope="module")
def index() -> ZoneIndex:
    return ZoneIndex()


def test_dataset_is_complete_and_valid() -> None:
    assert len(FEATURES) == 25
    assert len(GEOMS) == 25  # códigos únicos
    for code, geom in GEOMS.items():
        assert isinstance(geom, MultiPolygon), code
        assert geom.is_valid, code
        assert not geom.is_empty, code


def test_all_geofences_are_inside_colombia() -> None:
    for code, geom in GEOMS.items():
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        assert min_lon >= -79.1 and max_lon <= -66.8, code
        assert min_lat >= -4.3 and max_lat <= 12.6, code


@pytest.mark.parametrize(
    ("zone_type", "limit"),
    [("school", 30), ("high_risk", 40), ("urban", 50), ("highway", 90)],
)
def test_limit_matches_zone_type(zone_type: str, limit: int) -> None:
    codes = [f["properties"]["code"] for f in FEATURES if f["properties"]["zone_type"] == zone_type]
    assert codes
    # Excepción documentada: el tramo crítico del corredor (Guarne) tiene 60 por política.
    assert {LIMITS[c] for c in codes if c != "COR-GUARNE"} == {limit}


def test_urban_special_zones_lie_inside_their_city_perimeter() -> None:
    """Escolares y de alta siniestralidad están dentro del perímetro urbano de su ciudad."""
    for code in SPECIAL_ZONES:
        if code.startswith("COR-"):
            continue
        city = "BOG-URB" if code.startswith("BOG-") else "MED-URB"
        assert GEOMS[city].contains(GEOMS[code]), code


def test_point_in_city_center_gets_urban_limit(index: ZoneIndex) -> None:
    assert index.limit_at(*PLAZA_DE_BOLIVAR) == 50


def test_point_outside_every_geofence_has_no_limit(index: ZoneIndex) -> None:
    """Sin geocerca, Plata aplica el límite por defecto (90 km/h)."""
    assert index.limit_at(*VILLAVICENCIO) is None


@pytest.mark.parametrize("code", SPECIAL_ZONES)
def test_inside_a_special_zone_the_most_restrictive_limit_wins(index: ZoneIndex, code: str) -> None:
    """Un punto interior de una zona especial también está en el perímetro urbano (o en el
    corredor), pero se aplica el límite de la zona especial, que es el menor."""
    inside = GEOMS[code].representative_point()  # garantizado en el interior
    containing = [c for c, g in GEOMS.items() if g.contains(inside)]
    assert code in containing
    assert len(containing) >= 2  # la zona especial y la geocerca que la rodea
    assert index.limit_at(inside.y, inside.x) == min(LIMITS[c] for c in containing)
    assert index.limit_at(inside.y, inside.x) == LIMITS[code]


def test_point_on_the_border_is_not_within() -> None:
    """Un vértice de la zona escolar está sobre su borde: no cumple `within`, pero sí
    `intersects`. Es la semántica de ST_Within en PostGIS."""
    lon, lat = GEOMS["BOG-SCH-01"].geoms[0].exterior.coords[0]
    vertex = Point(lon, lat)
    assert not vertex.within(GEOMS["BOG-SCH-01"])
    assert vertex.intersects(GEOMS["BOG-SCH-01"])


def test_border_of_a_school_zone_gets_the_surrounding_urban_limit(index: ZoneIndex) -> None:
    """Sobre el borde de la zona escolar rige el límite urbano (50), no el escolar (30)."""
    lon, lat = GEOMS["BOG-SCH-01"].geoms[0].exterior.coords[0]
    assert index.limit_at(lat, lon) == 50


def test_a_few_meters_change_the_applied_limit(index: ZoneIndex) -> None:
    """Al cruzar el borde, el límite cambia: dentro de la zona escolar 30, fuera 50."""
    zone = GEOMS["BOG-SCH-01"]
    inside = zone.representative_point()
    outside = Point(zone.bounds[0] - 0.0005, inside.y)  # ~55 m al oeste del rectángulo
    assert index.limit_at(inside.y, inside.x) == 30
    assert index.limit_at(outside.y, outside.x) == 50
