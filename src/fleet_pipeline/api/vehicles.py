"""Consultas de vehículos con enmascaramiento según el rol (D-13)."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends

from fleet_pipeline.api.dependencies import get_connection
from fleet_pipeline.queries.positions import VehiclePosition, latest_positions
from fleet_pipeline.security.auth import Role, require_roles
from fleet_pipeline.security.db_roles import assume_db_role

router = APIRouter(prefix="/vehicles", tags=["vehículos"])


@router.get("/positions", response_model=list[VehiclePosition])
def get_positions(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    role: Annotated[Role, Depends(require_roles(Role.ADMIN, Role.ANALYST))],
) -> list[VehiclePosition]:
    """Última posición de cada vehículo (últimas 24 h).

    - **admin**: ID real (`VHC-8472-ABC`).
    - **analyst**: ID enmascarado (`VHC-****-ABC`) y `vehicle_key` para distinguir vehículos.
    """
    assume_db_role(conn, role)
    return latest_positions(conn, role)
