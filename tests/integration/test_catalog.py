"""Alta de rutas y flota: idempotente y con la geometría de la ruta en los dos sistemas."""

import psycopg
import pytest

from fleet_pipeline.simulator.catalog import register_fleet, register_routes
from fleet_pipeline.simulator.fleet import build_fleet
from fleet_pipeline.simulator.routes import load_routes

pytestmark = pytest.mark.integration


def test_registering_twice_changes_nothing(conn: psycopg.Connection) -> None:
    """Se ejecuta dentro de una transacción que se deshace al final (fixture `conn`)."""
    routes = load_routes()
    fleet = build_fleet(routes, seed=42)
    ids = [v.vehicle_id for v in fleet]

    def snapshot() -> tuple:
        return conn.execute(
            """
            SELECT (SELECT count(*) FROM silver.routes WHERE route_id = ANY(%s)),
                   (SELECT count(*) FROM bronze.vehicles WHERE vehicle_id = ANY(%s)),
                   (SELECT count(*) FROM silver.vehicle_routes WHERE vehicle_id = ANY(%s))
            """,
            (list(routes), ids, ids),
        ).fetchone()

    for _ in range(2):
        register_routes(conn, routes)
        register_fleet(conn, fleet)
        assert snapshot() == (len(routes), len(fleet), len(fleet))


def test_route_length_matches_postgis_geodesic_length(conn: psycopg.Connection) -> None:
    """La longitud calculada en Python (pyproj) coincide con la de PostGIS sobre el elipsoide,
    y la copia en EPSG:9377 (plano métrico) difiere menos de 0,5 %."""
    routes = load_routes()
    register_routes(conn, routes)
    rows = conn.execute(
        """
        SELECT route_id, length_m, ST_Length(geom::geography), ST_Length(geom_9377)
        FROM silver.routes WHERE route_id = ANY(%s)
        """,
        (list(routes),),
    ).fetchall()
    assert len(rows) == len(routes)
    for route_id, length_m, geodesic_m, planar_9377_m in rows:
        assert geodesic_m == pytest.approx(length_m, rel=1e-3), route_id
        assert planar_9377_m == pytest.approx(geodesic_m, rel=5e-3), route_id
