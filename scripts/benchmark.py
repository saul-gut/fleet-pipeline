"""Benchmark de consultas de BI sobre la tabla Oro en tres configuraciones (D-19):

1. Sin índices.
2. Con índices (GiST, GiST parcial, BRIN y B-tree).
3. Particionada por día + los mismos índices (como gold.fleet_events).

Replica las lecturas reales de gold.fleet_events corridas en el tiempo (un día por copia)
hasta el volumen pedido, en el esquema `bench`, sin tocar Oro. Mide cada consulta con
EXPLAIN ANALYZE: 1 corrida de calentamiento + mediana de 5. Los resultados se reportan tal
cual, también cuando una configuración empeora una consulta.

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
PLAIN = "bench.fleet_events"
PARTITIONED = "bench.fleet_events_part"

# {name} y {table} se completan para cada configuración.
INDEXES = {
    "GiST en geom": "CREATE INDEX {name}_geom ON {table} USING gist (geom)",
    "GiST parcial (solo alertas)": (
        "CREATE INDEX {name}_alerts ON {table} USING gist (geom) WHERE alert_type IS NOT NULL"
    ),
    "BRIN en ts": "CREATE INDEX {name}_ts ON {table} USING brin (ts)",
    "B-tree (vehicle_key, ts)": "CREATE INDEX {name}_vehicle_ts ON {table} (vehicle_key, ts)",
}


@dataclass(frozen=True)
class Query:
    name: str
    question: str
    sql: str  # {table} = tabla a consultar; {start} = primer día de los datos


QUERIES = [
    Query(
        "Q1 Heatmap",
        "Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días",
        """
        SELECT geom, alert_type, severity FROM {table}
        WHERE alert_type IS NOT NULL
          AND ts >= '{start}'::timestamptz AND ts < '{start}'::timestamptz + interval '7 days'
          AND geom && ST_MakeEnvelope(-74.11, 4.58, -74.06, 4.65, 4326)
        """,
    ),
    Query(
        "Q2 Ruta",
        "La ruta de un vehículo en un día, ordenada en el tiempo",
        """
        SELECT ts, geom, speed_kmh FROM {table}
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
        SELECT count(*) FROM {table} AS e
        JOIN silver.geofences AS g ON ST_Within(e.geom, g.geom)
        WHERE g.code = 'BOG-SCH-01'
        """,
    ),
    Query(
        "Q4 Vecinos",
        "Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->)",
        """
        SELECT vehicle_key, ts FROM {table}
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(-74.07609, 4.59808), 4326)
        LIMIT 10
        """,
    ),
]


@dataclass(frozen=True)
class Measurement:
    median_ms: float
    plan: str  # nodos principales del plan, para ver qué índices y particiones usó


@dataclass(frozen=True)
class IndexStat:
    label: str
    seconds: float
    size: str


# --- Carga ---------------------------------------------------------------------------


def load_plain(conn: psycopg.Connection, target_rows: int) -> tuple[int, datetime, float]:
    """Crea la tabla sin índices y la llena replicando Oro. Devuelve filas, primer día y
    segundos de carga."""
    base = conn.execute("SELECT count(*) FROM gold.fleet_events").fetchone()
    if not base or base[0] == 0:
        raise SystemExit(
            "gold.fleet_events está vacía: corre primero `uv run python tasks.py seed`"
        )
    copies = math.ceil(target_rows / base[0])
    log.info("Replicando %d filas de Oro %d veces (un día por copia)", base[0], copies)

    conn.execute("DROP SCHEMA IF EXISTS bench CASCADE")
    conn.execute("CREATE SCHEMA bench")
    conn.execute(f"CREATE TABLE {PLAIN} (LIKE gold.fleet_events INCLUDING DEFAULTS)")
    started = time.perf_counter()
    conn.execute(
        f"""
        INSERT INTO {PLAIN}
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
    conn.execute(f"VACUUM ANALYZE {PLAIN}")
    row = conn.execute(f"SELECT count(*), date_trunc('day', min(ts)) FROM {PLAIN}").fetchone()
    assert row is not None
    return row[0], row[1], load_s


def load_partitioned(conn: psycopg.Connection) -> tuple[int, float]:
    """Copia la tabla a una versión particionada por día. Devuelve particiones y segundos."""
    conn.execute(
        f"CREATE TABLE {PARTITIONED} (LIKE gold.fleet_events INCLUDING DEFAULTS) "
        "PARTITION BY RANGE (ts)"
    )
    row = conn.execute(
        f"""
        SELECT public.ensure_daily_partitions(
            %s::regclass,
            (min(ts) AT TIME ZONE 'America/Bogota')::date,
            (max(ts) AT TIME ZONE 'America/Bogota')::date)
        FROM {PLAIN}
        """,
        (PARTITIONED,),
    ).fetchone()
    started = time.perf_counter()
    conn.execute(f"INSERT INTO {PARTITIONED} SELECT * FROM {PLAIN} ORDER BY ts")
    load_s = time.perf_counter() - started
    conn.execute(f"VACUUM ANALYZE {PARTITIONED}")
    return (row[0] if row else 0), load_s


def create_indexes(conn: psycopg.Connection, table: str) -> list[IndexStat]:
    """Crea los índices de INDEXES en la tabla y mide tiempo y tamaño de cada uno."""
    name = table.replace(".", "_")
    stats = []
    for label, template in INDEXES.items():
        ddl = template.format(name=name, table=table)
        started = time.perf_counter()
        conn.execute(ddl)
        seconds = time.perf_counter() - started
        stats.append(IndexStat(label, seconds, size(conn, f"bench.{ddl.split()[2]}")))
    conn.execute(f"ANALYZE {table}")
    return stats


# --- Medición ------------------------------------------------------------------------


def measure_all(conn: psycopg.Connection, table: str, start: datetime) -> dict[str, Measurement]:
    results = {}
    for query in QUERIES:
        sql = query.sql.format(table=table, start=start.isoformat())
        explain = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
        conn.execute(explain)  # calentamiento: deja los datos en caché como en uso real
        times, plan = [], ""
        for _ in range(REPETITIONS):
            result = conn.execute(explain).fetchone()[0][0]
            times.append(result["Execution Time"])
            plan = plan or summarize_plan(result["Plan"])
        results[query.name] = Measurement(statistics.median(times), plan)
        log.info("%s · %s: %.1f ms", table, query.name, results[query.name].median_ms)
    return results


def summarize_plan(node: dict) -> str:
    """Nodos del plan con el índice usado, p. ej. 'Index Scan (…_geom)'. Un Append sobre
    particiones se resume como 'Append (N particiones)'."""
    label = node["Node Type"]
    if "Index Name" in node:
        label += f" ({node['Index Name']})"
    children = node.get("Plans", [])
    if label in {"Append", "Merge Append"} and children:
        return f"{label} ({len(children)} particiones) → {summarize_plan(children[0])}"
    summaries = [summarize_plan(child) for child in children]
    if label in {"Aggregate", "Gather", "Gather Merge", "Limit", "Sort", "Result"} and summaries:
        return " → ".join(summaries)
    return " → ".join([label, *summaries])


# --- Reporte -------------------------------------------------------------------------


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
    """Tamaño en disco; en tablas o índices particionados suma todas las particiones."""
    # pg_partition_tree no devuelve filas para tablas e índices sin particiones.
    row = conn.execute(
        """
        SELECT pg_size_pretty(coalesce(sum(pg_relation_size(relid)),
                                       pg_relation_size(%(rel)s::regclass)))
        FROM pg_partition_tree(%(rel)s::regclass)
        """,
        {"rel": relation},
    ).fetchone()
    return row[0] if row else "?"


@dataclass(frozen=True)
class Run:
    rows: int
    table_size: str
    load_s: float
    partitions: int
    version: str
    postgis: str
    plain: dict[str, Measurement]
    indexed: dict[str, Measurement]
    partitioned: dict[str, Measurement]
    index_stats: list[IndexStat]


def write_report(run: Run) -> None:
    lines = [
        f"### {thousands(run.rows)} filas",
        "",
        f"- Fecha: {datetime.now().astimezone():%Y-%m-%d %H:%M} · PostgreSQL {run.version} · "
        f"PostGIS {run.postgis} · Docker en Windows 11 (portátil)",
        f"- Tabla: {run.table_size} · carga sin índices: {run.load_s:.1f} s · "
        f"versión particionada: {run.partitions} particiones diarias",
        f"- Mediana de {REPETITIONS} ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento",
        "",
        "| Consulta | Pregunta de negocio | Sin índices | Con índices | Particionada + índices "
        "| Mejor vs. sin índices |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for query in QUERIES:
        base = run.plain[query.name].median_ms
        indexed = run.indexed[query.name].median_ms
        partitioned = run.partitioned[query.name].median_ms
        lines.append(
            f"| {query.name} | {query.question} | {thousands(base, 1)} ms "
            f"| {thousands(indexed, 1)} ms | {thousands(partitioned, 1)} ms "
            f"| {speedup(base, min(indexed, partitioned))} |"
        )
    lines += ["", "Planes de ejecución:", "", "| Consulta | Con índices | Particionada + índices |"]
    lines.append("|---|---|---|")
    for query in QUERIES:
        lines.append(
            f"| {query.name} | `{run.indexed[query.name].plan}` "
            f"| `{run.partitioned[query.name].plan}` |"
        )
    lines += ["", "| Índice | Tiempo de creación | Tamaño |", "|---|---:|---:|"]
    lines += [f"| {s.label} | {s.seconds:.1f} s | {s.size} |" for s in run.index_stats]
    lines.append("")

    header = (
        "# Benchmarks\n\nResultados reales generados por `scripts/benchmark.py` "
        "(no editar a mano). Cada sección corresponde a una corrida.\n\n"
    )
    previous = OUTPUT.read_text(encoding="utf-8").removeprefix(header) if OUTPUT.exists() else ""
    content = header + "\n".join(lines) + "\n" + previous
    OUTPUT.write_text(content.rstrip("\n") + "\n", encoding="utf-8")
    log.info("Resultados agregados a %s", OUTPUT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument(
        "--keep", action="store_true", help="conservar el esquema bench para inspeccionarlo"
    )
    args = parser.parse_args()

    with psycopg.connect(get_settings().conninfo, autocommit=True) as conn:
        version_row = conn.execute(
            "SELECT split_part(version(), ' ', 2), postgis_lib_version()"
        ).fetchone()
        assert version_row is not None
        rows, start, load_s = load_plain(conn, args.rows)
        table_size = size(conn, PLAIN)
        log.info("Cargadas %d filas en %.1f s (%s)", rows, load_s, table_size)

        plain = measure_all(conn, PLAIN, start)
        index_stats = create_indexes(conn, PLAIN)
        indexed = measure_all(conn, PLAIN, start)

        partitions, _ = load_partitioned(conn)
        create_indexes(conn, PARTITIONED)
        partitioned = measure_all(conn, PARTITIONED, start)

        if not args.keep:
            conn.execute("DROP SCHEMA bench CASCADE")  # libera el disco (~2 GB con 5 M)

    write_report(
        Run(
            rows,
            table_size,
            load_s,
            partitions,
            version_row[0],
            version_row[1],
            plain,
            indexed,
            partitioned,
            index_stats,
        )
    )


if __name__ == "__main__":
    main()
