"""Atajos de desarrollo multiplataforma (alternativa a Makefile en Windows).

Uso: uv run python tasks.py <tarea>
Cada tarea ejecuta comandos estándar; SETUP.md documenta los mismos comandos sin este script.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("tasks")

PSQL_IN_CONTAINER = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'


def run(*commands: list[str]) -> int:
    """Ejecuta comandos en orden y se detiene en el primero que falle."""
    for command in commands:
        log.info("$ %s", " ".join(command))
        code = subprocess.call(command)
        if code != 0:
            log.error("Falló con código %d", code)
            return code
    return 0


def up() -> int:
    """Levanta PostGIS y espera a que esté saludable."""
    return run(["docker", "compose", "up", "-d", "--wait"])


def down() -> int:
    """Detiene los contenedores. Los datos se conservan en el volumen."""
    return run(["docker", "compose", "down"])


def reset_db() -> int:
    """Borra el volumen y recrea la base desde db/init (destruye todos los datos)."""
    return run(["docker", "compose", "down", "-v"], ["docker", "compose", "up", "-d", "--wait"])


def psql() -> int:
    """Abre una consola psql dentro del contenedor."""
    return run(["docker", "compose", "exec", "db", "sh", "-c", PSQL_IN_CONTAINER])


def api() -> int:
    """Levanta la API en http://localhost:8000 (documentación en /docs)."""
    return run([sys.executable, "-m", "uvicorn", "fleet_pipeline.api.main:app", "--port", "8000"])


def dashboard() -> int:
    """Abre el centro de control en http://localhost:8501 (requiere la base con datos)."""
    return run(
        [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", "8501"]
    )


def seed() -> int:
    """Carga geocercas y flota, simula 24 h y procesa Plata y Oro (requiere la API corriendo)."""
    simulator = [sys.executable, "-m", "fleet_pipeline.simulator"]
    return run(
        [sys.executable, "-m", "fleet_pipeline.geofences", "load"],
        [*simulator, "register"],
        [*simulator, "run", "--backfill-hours", "24"],
        [sys.executable, "-m", "fleet_pipeline.processing", "all"],
    )


def process() -> int:
    """Procesa lo nuevo: Bronce -> Plata (geocercas, alertas) -> Oro (eventos, Driver Score)."""
    return run([sys.executable, "-m", "fleet_pipeline.processing", "all"])


def simulate() -> int:
    """Envía telemetría en vivo cada 10 s hasta Ctrl+C (requiere la API corriendo)."""
    return run([sys.executable, "-m", "fleet_pipeline.simulator", "run", "--live"])


def snapshot() -> int:
    """Exporta una foto de la capa Oro a data/snapshot/ para la demo pública (D-26)."""
    return run([sys.executable, "-m", "fleet_pipeline.queries.snapshot"])


def lint() -> int:
    """Revisa estilo y formato sin modificar archivos."""
    return run(["ruff", "check", "."], ["ruff", "format", "--check", "."])


def fmt() -> int:
    """Corrige estilo y formato automáticamente."""
    return run(["ruff", "check", "--fix", "."], ["ruff", "format", "."])


def test() -> int:
    """Ejecuta los tests con reporte de cobertura."""
    return run(["pytest", "--cov", "--cov-report=term-missing"])


TASKS: dict[str, Callable[[], int]] = {
    "up": up,
    "down": down,
    "reset-db": reset_db,
    "psql": psql,
    "api": api,
    "dashboard": dashboard,
    "seed": seed,
    "simulate": simulate,
    "process": process,
    "snapshot": snapshot,
    "lint": lint,
    "fmt": fmt,
    "test": test,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in TASKS:
        options = "\n".join(f"  {name:<9} {task.__doc__}" for name, task in TASKS.items())
        log.info("Uso: uv run python tasks.py <tarea>\n\nTareas:\n%s", options)
        return 2
    return TASKS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
