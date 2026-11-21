"""Dependencias compartidas por los endpoints."""

from collections.abc import Iterator

import psycopg
from fastapi import Request


def get_connection(request: Request) -> Iterator[psycopg.Connection]:
    """Una conexión del pool por petición. Al terminar sin errores hace commit; si algo
    falla hace rollback, así un lote se guarda completo o no se guarda."""
    with request.app.state.pool.connection() as conn:
        yield conn
