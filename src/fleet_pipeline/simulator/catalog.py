"""Alta administrativa de la flota simulada: rutas planificadas, vehículos y asignaciones.

No es telemetría: es la configuración que una empresa de flotas tendría en su sistema
(qué vehículos existen y qué ruta tiene asignada cada uno). Todo es idempotente.
"""

from __future__ import annotations

import psycopg

from fleet_pipeline.simulator.fleet import VehicleSpec
from fleet_pipeline.simulator.routes import Route


def register_routes(conn: psycopg.Connection, routes: dict[str, Route]) -> None:
    for route in routes.values():
        line_wkt = "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in route.coords) + ")"
        conn.execute(
            """
            INSERT INTO silver.routes (route_id, name, kind, length_m, geom, geom_9377)
            SELECT %(id)s, %(name)s, %(kind)s, %(length)s, g, ST_Transform(g, 9377)
            FROM (SELECT ST_GeomFromText(%(wkt)s, 4326) AS g) AS line
            ON CONFLICT (route_id) DO UPDATE SET
                name = EXCLUDED.name, kind = EXCLUDED.kind, length_m = EXCLUDED.length_m,
                geom = EXCLUDED.geom, geom_9377 = EXCLUDED.geom_9377
            """,
            {
                "id": route.route_id,
                "name": route.name,
                "kind": route.kind,
                "length": route.length_m,
                "wkt": line_wkt,
            },
        )


def register_fleet(conn: psycopg.Connection, fleet: list[VehicleSpec]) -> None:
    for spec in fleet:
        conn.execute(
            """
            INSERT INTO bronze.vehicles
                (vehicle_id, vehicle_type, tank_capacity_l, ref_consumption_l_100km)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (vehicle_id) DO NOTHING
            """,
            (
                spec.vehicle_id,
                spec.model.vehicle_type,
                spec.model.tank_capacity_l,
                spec.model.consumption_l_100km,
            ),
        )
        conn.execute(
            """
            INSERT INTO silver.vehicle_routes (vehicle_id, route_id) VALUES (%s, %s)
            ON CONFLICT (vehicle_id) DO UPDATE SET route_id = EXCLUDED.route_id
            """,
            (spec.vehicle_id, spec.route_id),
        )
