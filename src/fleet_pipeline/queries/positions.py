"""Última posición conocida de cada vehículo, enmascarada según el rol."""

from datetime import datetime

import psycopg
from psycopg import sql
from psycopg.rows import class_row
from pydantic import BaseModel

from fleet_pipeline.security.auth import Role

# Cada rol lee su propia vista: el admin la de Bronce (ID real) y el analista la de
# analytics (ID enmascarado). Los nombres vienen de este mapa fijo, nunca de la petición.
POSITIONS_VIEW_BY_ROLE: dict[Role, tuple[str, str]] = {
    Role.ADMIN: ("bronze", "v_latest_positions"),
    Role.ANALYST: ("analytics", "latest_positions"),
}


class VehiclePosition(BaseModel):
    vehicle_key: int
    vehicle_id: str
    vehicle_type: str
    ts: datetime
    lat: float
    lon: float
    speed_kmh: float
    fuel_pct: float
    fuel_l: float


def latest_positions(conn: psycopg.Connection, role: Role) -> list[VehiclePosition]:
    """Posiciones de las últimas 24 h. El rol de base de datos ya debe estar asumido."""
    query = sql.SQL(
        """
        SELECT vehicle_key, vehicle_id, vehicle_type, ts, lat, lon,
               speed_kmh::float8, fuel_pct::float8, fuel_l::float8
        FROM {view}
        ORDER BY vehicle_key
        """
    ).format(view=sql.Identifier(*POSITIONS_VIEW_BY_ROLE[role]))
    with conn.cursor(row_factory=class_row(VehiclePosition)) as cur:
        return cur.execute(query).fetchall()
