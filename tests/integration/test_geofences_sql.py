"""Cruce punto-polígono en PostGIS: el mismo resultado que Shapely, con y sin subdivisión.

La capa Plata cruza cada lectura contra `silver.geofence_parts` (polígonos subdivididos en
piezas de hasta 256 vértices, D-16) con ST_Within. Estos tests comprueban que esa
optimización no cambia ninguna respuesta y que la semántica del borde es la documentada.
"""

import json
import random

import psycopg
import pytest
from shapely.geometry import Point, shape

from fleet_pipeline.geofences.loader import read_features

pytestmark = pytest.mark.integration

FEATURES = read_features()
GEOMS = {f["properties"]["code"]: shape(f["geometry"]) for f in FEATURES}

# Rectángulos de Bogotá y del Valle de Aburrá (lon_min, lat_min, lon_max, lat_max).
STUDY_AREAS = [(-74.25, 4.45, -73.99, 4.83), (-75.70, 6.10, -75.45, 6.40)]
POINTS_PER_AREA = 1500


def _random_points() -> list[tuple[float, float]]:
    rng = random.Random(9377)  # semilla fija: el test es reproducible
    return [
        (rng.uniform(lon0, lon1), rng.uniform(lat0, lat1))
        for lon0, lat0, lon1, lat1 in STUDY_AREAS
        for _ in range(POINTS_PER_AREA)
    ]


def _codes_by_point(conn: psycopg.Connection, table: str, points: list) -> dict[int, set[str]]:
    """Geocercas que contienen cada punto, según PostGIS (por piezas o por geocerca entera)."""
    join = (
        "silver.geofence_parts AS p JOIN silver.geofences AS g USING (geofence_id)"
        if table == "parts"
        else "silver.geofences AS g"
    )
    geom = "p.geom" if table == "parts" else "g.geom"
    rows = conn.execute(
        f"""
        SELECT pt.i, g.code
        FROM unnest(%s::float8[], %s::float8[]) WITH ORDINALITY AS pt(lon, lat, i)
        JOIN {join} ON ST_Within(ST_SetSRID(ST_MakePoint(pt.lon, pt.lat), 4326), {geom})
        """,
        ([p[0] for p in points], [p[1] for p in points]),
    ).fetchall()
    result: dict[int, set[str]] = {}
    for i, code in rows:
        result.setdefault(i - 1, set()).add(code)
    return result


def test_all_geofences_are_loaded_valid_and_subdivided(
    conn_autocommit: psycopg.Connection,
) -> None:
    row = conn_autocommit.execute(
        """
        SELECT count(*), bool_and(ST_IsValid(geom)), min(ST_SRID(geom)), max(ST_SRID(geom)),
               (SELECT count(*) FROM silver.geofence_parts),
               (SELECT max(ST_NPoints(geom)) FROM silver.geofence_parts)
        FROM silver.geofences
        """
    ).fetchone()
    assert row is not None
    count, all_valid, srid_min, srid_max, parts, max_vertices = row
    assert (count, all_valid, srid_min, srid_max) == (25, True, 4326, 4326)
    assert parts > count  # el perímetro urbano se partió en piezas
    assert max_vertices <= 256


def test_postgis_agrees_with_shapely_point_by_point(conn_autocommit: psycopg.Connection) -> None:
    """3 000 puntos al azar en Bogotá y Medellín: PostGIS y Shapely encuentran exactamente
    las mismas geocercas para cada punto."""
    points = _random_points()
    in_postgis = _codes_by_point(conn_autocommit, "geofences", points)
    in_shapely = {
        i: codes
        for i, (lon, lat) in enumerate(points)
        if (codes := {c for c, g in GEOMS.items() if Point(lon, lat).within(g)})
    }
    assert in_postgis == in_shapely
    # El muestreo cubre los casos que importan: urbano y zonas especiales.
    hit_codes = set().union(*in_postgis.values())
    assert {"BOG-URB", "MED-URB"} <= hit_codes
    assert any("-SCH-" in c or "-HR-" in c for c in hit_codes)


def test_subdivided_parts_give_the_same_answer(conn_autocommit: psycopg.Connection) -> None:
    """ST_Subdivide acelera el cruce (D-16) sin cambiar ni un resultado."""
    points = _random_points()
    assert _codes_by_point(conn_autocommit, "parts", points) == _codes_by_point(
        conn_autocommit, "geofences", points
    )


def test_border_semantics_within_vs_intersects(conn_autocommit: psycopg.Connection) -> None:
    """Un vértice de la zona escolar: ST_Within y ST_Contains dicen que no, ST_Intersects y
    ST_Covers que sí. El pipeline usa ST_Within: el borde no cuenta como dentro."""
    lon, lat = GEOMS["BOG-SCH-01"].geoms[0].exterior.coords[0]
    row = conn_autocommit.execute(
        """
        WITH v AS (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt)
        SELECT ST_Within(v.pt, g.geom), ST_Contains(g.geom, v.pt),
               ST_Intersects(v.pt, g.geom), ST_Covers(g.geom, v.pt)
        FROM silver.geofences AS g, v
        WHERE g.code = 'BOG-SCH-01'
        """,
        (lon, lat),
    ).fetchone()
    assert row == (False, False, True, True)


def test_gist_index_is_used_for_the_point_in_polygon_join(
    conn_autocommit: psycopg.Connection,
) -> None:
    """El plan de la consulta usa el índice GiST de las piezas, no un barrido completo."""
    conn_autocommit.execute("SET enable_seqscan = off")  # tabla chica: forzar el plan de escala
    try:
        plan = conn_autocommit.execute(
            """
            EXPLAIN (FORMAT JSON)
            SELECT p.geofence_id
            FROM silver.geofence_parts AS p
            WHERE ST_Within(ST_SetSRID(ST_MakePoint(-74.0761, 4.5981), 4326), p.geom)
            """
        ).fetchone()
    finally:
        conn_autocommit.execute("RESET enable_seqscan")
    assert plan is not None
    assert "geofence_parts_geom_gist" in json.dumps(plan[0])
