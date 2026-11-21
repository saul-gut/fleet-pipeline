"""Construye data/geofences/geofences.geojson a partir de las fuentes de data/raw/ (D-06, D-15).

Requisitos: haber corrido scripts/download_sources.py y tener PostGIS corriendo (el clustering
DBSCAN y las operaciones métricas se hacen en la base, en EPSG:9377).

Uso: uv run --group prep python scripts/prepare_geofences.py
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import psycopg
from shapely.geometry import mapping

from fleet_pipeline.config import get_settings
from fleet_pipeline.simulator.routes import load_routes

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("prepare_geofences")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUTPUT = ROOT / "data" / "geofences" / "geofences.geojson"

SIMPLIFY_TOLERANCE_M = 5.0
HOTSPOT_BUFFER_M = 50.0
SCHOOL_BUFFER_M = 100.0
CORRIDOR_HALF_WIDTH_M = 30.0
TOP_CLUSTERS = 5
MAX_REPEATS_PER_COORDINATE = 150  # D-15: más repeticiones = ubicación por defecto

GUARNE = (6.2780, -75.4440)
GUARNE_HALF_LENGTH_M = 8_500.0  # tramo de ~17 km
EL_SANTUARIO = (6.1370, -75.2650)
SANTUARIO_HAZARD_LENGTH_M = 135_000.0  # "unos 135 km del corredor" hacia Bogotá

SRC_BOG_PERIMETER = "Perímetro urbano, Datos Abiertos Bogotá (CC BY 4.0)"
SRC_MED_PERIMETER = "Perímetro urbano POT, Alcaldía de Medellín / GeoMedellín (CC)"
SRC_BOG_CRASHES = "Clusters DBSCAN de muertos 2023-2026, SIGAT – Secretaría Distrital de Movilidad"
SRC_MED_CRASHES = "Clusters DBSCAN de heridos 2025, VM_Accidentes – Alcaldía de Medellín"
SRC_BOG_SCHOOLS = "Clusters DBSCAN de colegios, Datos Abiertos Bogotá (CC BY 4.0)"
SRC_MED_SCHOOLS = "Clusters DBSCAN de sedes educativas, GeoMedellín (CC BY-SA 4.0)"
SRC_CORRIDOR = "Ruta OSRM / © colaboradores de OpenStreetMap (ODbL); tramos según reportes viales"


@dataclass(frozen=True)
class ClusterParams:
    eps_m: float
    min_points: int


CRASH_PARAMS = {"bogota": ClusterParams(250, 8), "medellin": ClusterParams(75, 100)}
SCHOOL_PARAMS = {"bogota": ClusterParams(300, 8), "medellin": ClusterParams(300, 5)}

Point = tuple[float, float]  # (lon, lat)


# --- Lectura de fuentes ---------------------------------------------------------------------


def read_layer_4326(path: Path) -> gpd.GeoDataFrame:
    """Lee cualquier capa y la reproyecta a 4326 usando la definición de su propio .prj."""
    layer = gpd.read_file(path)
    return layer.to_crs(4326)


def points_of(layer: gpd.GeoDataFrame) -> list[Point]:
    return [(p.x, p.y) for p in layer.geometry if p is not None and not p.is_empty]


def drop_stacked_points(points: list[Point], max_repeats: int) -> list[Point]:
    """Excluye coordenadas repetidas más de `max_repeats` veces (geocodificación por defecto)."""
    counts = Counter((round(lon, 6), round(lat, 6)) for lon, lat in points)
    stacked = {coord for coord, n in counts.items() if n > max_repeats}
    kept = [p for p in points if (round(p[0], 6), round(p[1], 6)) not in stacked]
    log.info(
        "Depuración: %d coordenadas apiladas excluidas (%d de %d puntos)",
        len(stacked),
        len(points) - len(kept),
        len(points),
    )
    return kept


# --- Operaciones en PostGIS --------------------------------------------------------------


def geojson_4326(expression: str) -> str:
    """SQL que convierte una geometría en 9377 a GeoJSON 4326 válido con 6 decimales (~0,1 m).

    ST_ReducePrecision redondea a la misma grilla que el GeoJSON y garantiza un resultado
    válido; redondear solo al escribir puede crear autointersecciones.
    """
    return (
        "ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_ReducePrecision("
        f"ST_Transform({expression}, 4326), 0.000001), 3)), 6)"
    )


def load_points(conn: psycopg.Connection, table: str, points: Iterable[Point]) -> None:
    conn.execute(f"CREATE TEMP TABLE {table} (geom geometry(Point, 4326)) ON COMMIT DROP")
    with conn.cursor().copy(f"COPY {table} (geom) FROM STDIN") as copy:
        for lon, lat in points:
            copy.write_row([f"SRID=4326;POINT({lon} {lat})"])
    # COPY recibe 4326; se transforma una sola vez a metros (9377) para todo lo demás.
    conn.execute(
        f"ALTER TABLE {table} ALTER COLUMN geom TYPE geometry(Point, 9377) "
        f"USING ST_Transform(geom, 9377)"
    )


def top_clusters(
    conn: psycopg.Connection, table: str, params: ClusterParams, zone_sql: str
) -> list[tuple[int, dict[str, Any]]]:
    """Agrupa con DBSCAN y devuelve los clusters más grandes como (n_puntos, GeoJSON 4326).

    `zone_sql` define el polígono de cada cluster a partir de `g` (los puntos del cluster).
    """
    rows = conn.execute(
        f"""
        WITH clustered AS (
            SELECT ST_ClusterDBSCAN(geom, eps => %s, minpoints => %s) OVER () AS cid, geom AS g
            FROM {table}
        ), zones AS (
            SELECT count(*) AS n, {zone_sql} AS zone
            FROM clustered
            WHERE cid IS NOT NULL
            GROUP BY cid
            ORDER BY n DESC
            LIMIT %s
        )
        SELECT n, {geojson_4326("zone")} FROM zones ORDER BY n DESC
        """,
        (params.eps_m, params.min_points, TOP_CLUSTERS),
    ).fetchall()
    return [(n, json.loads(geojson)) for n, geojson in rows]


def clean_polygon(conn: psycopg.Connection, geometry: dict[str, Any]) -> dict[str, Any]:
    """Une, valida y simplifica un polígono en metros (EPSG:9377), y lo devuelve en 4326."""
    row = conn.execute(
        f"""
        SELECT {
            geojson_4326('''ST_SimplifyPreserveTopology(
                   ST_Transform(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 9377),
                   %s)''')
        }
        """,
        (json.dumps(geometry), SIMPLIFY_TOLERANCE_M),
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def corridor_zones(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    """Autopista completa, tramo de Guarne y tramo El Santuario–Caño Alegre (sobre la ruta)."""
    route = load_routes()["COR-01"]  # sentido Bogotá -> Medellín
    line_wkt = "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in route.coords) + ")"
    row = conn.execute(
        f"""
        WITH line AS (
            SELECT ST_Transform(ST_GeomFromText(%(line)s, 4326), 9377) AS g
        ), places AS (
            SELECT
                ST_Transform(ST_SetSRID(ST_MakePoint(%(g_lon)s, %(g_lat)s), 4326), 9377)
                    AS guarne,
                ST_Transform(ST_SetSRID(ST_MakePoint(%(s_lon)s, %(s_lat)s), 4326), 9377)
                    AS santuario
        ), marks AS (
            SELECT g, ST_Length(g) AS len,
                   ST_LineLocatePoint(g, guarne) AS f_guarne,
                   ST_LineLocatePoint(g, santuario) AS f_santuario
            FROM line, places
        )
        SELECT
            {geojson_4326("ST_Buffer(g, %(w)s)")},
            {
            geojson_4326('''ST_Buffer(ST_LineSubstring(g,
                greatest(f_guarne - %(gh)s / len, 0), least(f_guarne + %(gh)s / len, 1)), %(w)s)''')
        },
            {
            geojson_4326('''ST_Buffer(ST_LineSubstring(g,
                greatest(f_santuario - %(sl)s / len, 0), f_santuario), %(w)s)''')
        },
            ST_AsText(ST_Transform(
                ST_LineInterpolatePoint(g, greatest(f_santuario - %(sl)s / len, 0)), 4326))
        FROM marks
        """,
        {
            "line": line_wkt,
            "g_lat": GUARNE[0],
            "g_lon": GUARNE[1],
            "s_lat": EL_SANTUARIO[0],
            "s_lon": EL_SANTUARIO[1],
            "w": CORRIDOR_HALF_WIDTH_M,
            "gh": GUARNE_HALF_LENGTH_M,
            "sl": SANTUARIO_HAZARD_LENGTH_M,
        },
    ).fetchone()
    assert row is not None
    log.info("Fin del tramo El Santuario–Caño Alegre (135 km hacia Bogotá): %s", row[3])
    return {
        "highway": json.loads(row[0]),
        "guarne": json.loads(row[1]),
        "hazard": json.loads(row[2]),
    }


# --- Ensamble ----------------------------------------------------------------------------


def feature(
    code: str,
    name: str,
    city: str,
    zone_type: str,
    limit: int,
    limit_source: str,
    source: str,
    geometry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "code": code,
            "name": name,
            "city": city,
            "zone_type": zone_type,
            "speed_limit_kmh": limit,
            "limit_source": limit_source,
            "source": source,
        },
        "geometry": geometry,
    }


def build(conn: psycopg.Connection) -> list[dict[str, Any]]:
    features = []

    bog_perimeter = read_layer_4326(RAW / "perimetrourbano_bogota" / "PerimetroUrbano.shp")
    med_perimeter = read_layer_4326(
        RAW / "perimetrourbano_medellin" / "perimetro_urbano_pot.geojson"
    )
    features.append(
        feature(
            "BOG-URB",
            "Perímetro urbano de Bogotá",
            "bogota",
            "urban",
            50,
            "law",
            SRC_BOG_PERIMETER,
            clean_polygon(conn, mapping(bog_perimeter.union_all())),
        )
    )
    features.append(
        feature(
            "MED-URB",
            "Perímetro urbano de Medellín (incluye 3 cabeceras)",
            "medellin",
            "urban",
            50,
            "law",
            SRC_MED_PERIMETER,
            clean_polygon(conn, mapping(med_perimeter.union_all())),
        )
    )

    crashes = {
        "bogota": points_of(read_layer_4326(RAW / "siniestros_bogota" / "muertos.geojson")),
        "medellin": drop_stacked_points(
            points_of(read_layer_4326(RAW / "siniestros_medellin" / "heridos.geojson")),
            MAX_REPEATS_PER_COORDINATE,
        ),
    }
    schools = {
        "bogota": points_of(
            read_layer_4326(RAW / "colegios_bogota" / "Colegios12.24" / "Colegios12_2024.shp")
        ),
        "medellin": points_of(
            read_layer_4326(RAW / "colegios_medellin" / "sedes_educativas.geojson")
        ),
    }
    crash_sources = {"bogota": SRC_BOG_CRASHES, "medellin": SRC_MED_CRASHES}
    school_sources = {"bogota": SRC_BOG_SCHOOLS, "medellin": SRC_MED_SCHOOLS}
    prefix = {"bogota": "BOG", "medellin": "MED"}
    victims = {"bogota": "muertos", "medellin": "heridos"}

    for city in ("bogota", "medellin"):
        load_points(conn, f"crashes_{city}", crashes[city])
        hotspot_sql = f"ST_Buffer(ST_ConvexHull(ST_Collect(g)), {HOTSPOT_BUFFER_M})"
        for rank, (n, geom) in enumerate(
            top_clusters(conn, f"crashes_{city}", CRASH_PARAMS[city], hotspot_sql), 1
        ):
            log.info("%s hotspot #%d: %d %s", city, rank, n, victims[city])
            features.append(
                feature(
                    f"{prefix[city]}-HR-{rank:02d}",
                    f"Hotspot de siniestralidad #{rank} ({n} {victims[city]})",
                    city,
                    "high_risk",
                    40,
                    "fleet_policy",
                    crash_sources[city],
                    geom,
                )
            )

        load_points(conn, f"schools_{city}", schools[city])
        school_sql = f"ST_Union(ST_Buffer(g, {SCHOOL_BUFFER_M}))"
        for rank, (n, geom) in enumerate(
            top_clusters(conn, f"schools_{city}", SCHOOL_PARAMS[city], school_sql), 1
        ):
            log.info("%s zona escolar #%d: %d colegios", city, rank, n)
            features.append(
                feature(
                    f"{prefix[city]}-SCH-{rank:02d}",
                    f"Zona escolar #{rank} ({n} colegios)",
                    city,
                    "school",
                    30,
                    "law",
                    school_sources[city],
                    geom,
                )
            )

    corridor = corridor_zones(conn)
    features.append(
        feature(
            "COR-HWY",
            "Autopista Medellín–Bogotá (Ruta 60)",
            "corridor",
            "highway",
            90,
            "law",
            SRC_CORRIDOR,
            corridor["highway"],
        )
    )
    features.append(
        feature(
            "COR-GUARNE",
            "Tramo de alta siniestralidad de Guarne (~17 km)",
            "corridor",
            "high_risk",
            60,
            "fleet_policy",
            SRC_CORRIDOR,
            corridor["guarne"],
        )
    )
    features.append(
        feature(
            "COR-SANTUARIO",
            "Tramo deteriorado El Santuario–Caño Alegre (~135 km)",
            "corridor",
            "road_hazard",
            70,
            "fleet_policy",
            SRC_CORRIDOR,
            corridor["hazard"],
        )
    )
    return features


def main() -> None:
    with psycopg.connect(get_settings().conninfo) as conn:
        features = build(conn)
        conn.rollback()  # solo se usaron tablas temporales
    OUTPUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info(
        "Guardadas %d geocercas en %s (%.0f KB)",
        len(features),
        OUTPUT,
        OUTPUT.stat().st_size / 1024,
    )


if __name__ == "__main__":
    main()
