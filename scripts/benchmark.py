"""Benchmark de consultas de BI sobre la tabla Oro: sin índices vs con índices (D-19).

Replica las lecturas reales de gold.fleet_events corridas en el tiempo (un día por copia)
hasta el volumen pedido, en el esquema `bench`, sin tocar Oro. Mide cada consulta con
EXPLAIN ANALYZE: 1 corrida de calentamiento + mediana de 5.

Uso: uv run python scripts/benchmark.py --rows 1000000
Requisito: gold.fleet_events con datos (uv run python tasks.py seed).
"""

from __future__ import annotations

import argparse
import logging
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

from fleet_pipeline.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("benchmark")

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "benchmarks.md"
REPETITIONS = 5

INDEXES = {
    "GiST en geom": "CREATE INDEX bench_geom_gist ON bench.fleet_events USING gist (geom)",
    "GiST parcial (solo alertas)": (
        "CREATE INDEX bench_alerts_gist ON bench.fleet_events USING gist (geom) "
        "WHERE alert_type IS NOT NULL"
    ),
    "BRIN en ts": "CREATE INDEX bench_ts_brin ON bench.fleet_events USING brin (ts)",
    "B-tree (vehicle_key, ts)": (
        "CREATE INDEX bench_vehicle_ts ON bench.fleet_events (vehicle_key, ts)"
    ),
}


@dataclass(frozen=True)
class Query:
    name: str
    question: str
    sql: str


# {start} es el primer día de los datos replicados; se calcula al cargar.
QUERIES = [
    Query(
        "Q1 Heatmap",
        "Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días",
        """
        SELECT geom, alert_type, severity FROM bench.fleet_events
        WHERE alert_type IS NOT NULL
          AND ts >= '{start}'::timestamptz AND ts < '{start}'::timestamptz + interval '7 days'
          AND geom && ST_MakeEnvelope(-74.11, 4.58, -74.06, 4.65, 4326)
        """,
    ),
    Query(
        "Q2 Ruta",
        "La ruta de un vehículo en un día, ordenada en el tiempo",
        """
        SELECT ts, geom, speed_kmh FROM bench.fleet_events
        WHERE vehicle_key = 3
          AND ts >= '{start}'::timestamptz + interval '3 days'
          AND ts < '{start}'::timestamptz + interval '4 days'
        ORDER BY ts
        """,
    ),
    Query(
        "Q3 Geocerca",
        "Cuántos eventos ocurrieron dentro de la zona escolar BOG-SCH-01 (ST_Within)",
        """
        SELECT count(*) FROM bench.fleet_events AS e
        JOIN silver.geofences AS g ON ST_Within(e.geom, g.geom)
        WHERE g.code = 'BOG-SCH-01'
        """,
    ),
    Query(
        "Q4 Vecinos",
        "Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->)",
        """
        SELECT vehicle_key, ts FROM bench.fleet_events
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(-74.07609, 4.59808), 4326)
        LIMIT 10
        """,
    ),
]


@dataclass(frozen=True)
class Measurement:
    median_ms: float
    plan: str  # nodos principales del plan, para ver si usó índices


def load(conn: psycopg.Connection, target_rows: int) -> tuple[int, datetime, float]:
    """Crea bench.fleet_events sin índices y la llena replicando Oro. Devuelve filas,
    primer día y segundos de carga."""
    base = conn.execute("SELECT count(*) FROM gold.fleet_events").fetchone()
    if not base or base[0] == 0:
        raise SystemExit(
            "gold.fleet_events está vacía: corre primero `uv run python tasks.py seed`"
        )
    copies = math.ceil(target_rows / base[0])
    log.info("Replicando %d filas de Oro %d veces (un día por copia)", base[0], copies)

    conn.execute("DROP SCHEMA IF EXISTS bench CASCADE")
    conn.execute("CREATE SCHEMA bench")
    conn.execute("CREATE TABLE bench.fleet_events (LIKE gold.fleet_events INCLUDING DEFAULTS)")
    started = time.perf_counter()
    conn.execute(
        """
        INSERT INTO bench.fleet_events
        SELECT e.vehicle_key, e.vehicle_id_masked, e.vehicle_type,
               e.ts + make_interval(days => k), e.event_date + k, e.geom, e.speed_kmh,
               e.speed_limit_kmh, e.excess_kmh, e.is_over_threshold, e.zone_type,
               e.geofence_code, e.alert_id, e.alert_type, e.severity, e.fuel_pct, e.fuel_l,
               e.remaining_km, e.is_low_fuel, e.route_id, e.step_m
        FROM generate_series(0, %s - 1) AS k
        CROSS JOIN gold.fleet_events AS e
        ORDER BY k, e.ts  -- orden temporal, como llegan los datos reales (importa para BRIN)
        """,
        (copies,),
    )
    load_s = time.perf_counter() - started
    conn.execute("VACUUM ANALYZE bench.fleet_events")
    rows, start = conn.execute(
        "SELECT count(*), date_trunc('day', min(ts)) FROM bench.fleet_events"
    ).fetchone()
    return rows, start, load_s


def measure(conn: psycopg.Connection, query: Query, start: datetime) -> Measurement:
    sql = query.sql.format(start=start.isoformat())
    explain = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
    conn.execute(explain)  # calentamiento: deja los datos en caché como en uso real
    times, plan = [], ""
    for _ in range(REPETITIONS):
        result = conn.execute(explain).fetchone()[0][0]
        times.append(result["Execution Time"])
        plan = plan or summarize_plan(result["Plan"])
    return Measurement(statistics.median(times), plan)


def summarize_plan(node: dict, depth: int = 0) -> str:
    """Tipos de nodo del plan con el índice usado, p. ej. 'Index Scan (bench_geom_gist)'."""
    label = node["Node Type"]
    if "Index Name" in node:
        label += f" ({node['Index Name']})"
    children = [summarize_plan(child, depth + 1) for child in node.get("Plans", [])]
    relevant = [c for c in children if c]
    if label in {"Aggregate", "Gather", "Gather Merge", "Limit", "Sort", "Result"} and relevant:
        return " → ".join(relevant)
    return " → ".join([label, *relevant]) if relevant else label


def thousands(value: float, decimals: int = 0) -> str:
    """Formato con espacio como separador de miles (1 015 776)."""
    return f"{value:,.{decimals}f}".replace(",", " ")


def speedup(before_ms: float, after_ms: float) -> str:
    """'17×' si mejora; 'más lenta (0,7×)' si empeora: los resultados se reportan tal cual."""
    ratio = before_ms / after_ms
    if ratio < 1:
        return f"**más lenta** ({ratio:.1f}×)".replace(".", ",")
    return f"{thousands(ratio)}×" if ratio >= 10 else f"{ratio:.1f}×".replace(".", ",")


def size(conn: psycopg.Connection, relation: str) -> str:
    return conn.execute("SELECT pg_size_pretty(pg_relation_size(%s))", (relation,)).fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    args = parser.parse_args()

    with psycopg.connect(get_settings().conninfo, autocommit=True) as conn:
        version = conn.execute("SELECT split_part(version(), ' ', 2)").fetchone()[0]
        postgis = conn.execute("SELECT postgis_lib_version()").fetchone()[0]
        rows, start, load_s = load(conn, args.rows)
        table_size = size(conn, "bench.fleet_events")
        log.info("Cargadas %d filas en %.1f s (%s)", rows, load_s, table_size)

        before = {q.name: measure(conn, q, start) for q in QUERIES}
        for name, m in before.items():
            log.info("Sin índices · %s: %.1f ms", name, m.median_ms)

        index_stats = []
        for label, ddl in INDEXES.items():
            started = time.perf_counter()
            conn.execute(ddl)
            index_name = ddl.split()[2]
            index_stats.append(
                (label, time.perf_counter() - started, size(conn, f"bench.{index_name}"))
            )
        conn.execute("ANALYZE bench.fleet_events")

        after = {q.name: measure(conn, q, start) for q in QUERIES}
        for name, m in after.items():
            log.info("Con índices · %s: %.1f ms", name, m.median_ms)

    write_report(rows, table_size, load_s, version, postgis, before, after, index_stats)


def write_report(
    rows: int,
    table_size: str,
    load_s: float,
    version: str,
    postgis: str,
    before: dict[str, Measurement],
    after: dict[str, Measurement],
    index_stats: list[tuple[str, float, str]],
) -> None:
    lines = [
        f"### {thousands(rows)} filas",
        "",
        f"- Fecha: {datetime.now().astimezone():%Y-%m-%d %H:%M} · PostgreSQL {version} · "
        f"PostGIS {postgis} · Docker en Windows 11 (portátil)",
        f"- Tabla: {table_size} · carga sin índices: {load_s:.1f} s",
        f"- Mediana de {REPETITIONS} ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento",
        "",
        "| Consulta | Pregunta de negocio | Sin índices | Con índices | Mejora "
        "| Plan con índices |",
        "|---|---|---:|---:|---:|---|",
    ]
    for query in QUERIES:
        b, a = before[query.name], after[query.name]
        lines.append(
            f"| {query.name} | {query.question} | {thousands(b.median_ms, 1)} ms "
            f"| {thousands(a.median_ms, 1)} ms | {speedup(b.median_ms, a.median_ms)} "
            f"| `{a.plan}` |"
        )
    lines += ["", "| Índice | Tiempo de creación | Tamaño |", "|---|---:|---:|"]
    lines += [f"| {label} | {secs:.1f} s | {sz} |" for label, secs, sz in index_stats]
    lines.append("")

    header = (
        "# Benchmarks\n\nResultados reales generados por `scripts/benchmark.py` "
        "(no editar a mano). Cada sección corresponde a una corrida.\n\n"
    )
    previous = OUTPUT.read_text(encoding="utf-8").removeprefix(header) if OUTPUT.exists() else ""
    OUTPUT.write_text(header + "\n".join(lines) + "\n" + previous, encoding="utf-8")
    log.info("Resultados agregados a %s", OUTPUT)


if __name__ == "__main__":
    main()
