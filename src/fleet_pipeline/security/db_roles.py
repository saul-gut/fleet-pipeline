"""Traducción del rol de la API al rol de PostgreSQL (defensa en profundidad, D-13).

Cada petición asume en la base el rol de quien la hace. Aunque el código de la aplicación
tuviera un error, PostgreSQL seguiría negando al analista el acceso al ID real.
"""

import psycopg
from psycopg import sql

from fleet_pipeline.security.auth import Role

DB_ROLE_BY_API_ROLE: dict[Role, str] = {
    Role.DEVICE: "fleet_ingest",
    Role.ADMIN: "bi_admin",
    Role.ANALYST: "bi_analyst",
}


def assume_db_role(conn: psycopg.Connection, role: Role) -> None:
    """Asume el rol de base de datos hasta el fin de la transacción actual (SET LOCAL)."""
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(DB_ROLE_BY_API_ROLE[role])))
