"""Carga de geocercas: idempotente y con piezas regeneradas (D-16)."""

import psycopg
import pytest

from fleet_pipeline.geofences.loader import MAX_VERTICES_PER_PART, load_geofences, read_features

pytestmark = pytest.mark.integration


def test_loading_twice_keeps_one_row_per_geofence(conn: psycopg.Connection) -> None:
    """Dentro de una transacción que se deshace al final: no altera la base de los demás."""
    features = read_features()
    for _ in range(2):
        assert load_geofences(conn, features) == len(features)
        row = conn.execute(
            """
            SELECT (SELECT count(*) FROM silver.geofences),
                   (SELECT count(DISTINCT geofence_id) FROM silver.geofence_parts),
                   (SELECT max(ST_NPoints(geom)) FROM silver.geofence_parts)
            """
        ).fetchone()
        assert row is not None
        geofences, with_parts, max_vertices = row
        assert geofences == with_parts == len(features)
        assert max_vertices <= MAX_VERTICES_PER_PART
