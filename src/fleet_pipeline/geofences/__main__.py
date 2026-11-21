"""Uso: uv run python -m fleet_pipeline.geofences load"""

import argparse
import logging

import psycopg

from fleet_pipeline.config import get_settings
from fleet_pipeline.geofences.loader import load_geofences, read_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(prog="geofences")
    parser.add_argument("command", choices=["load"], help="carga el GeoJSON en silver.geofences")
    parser.parse_args()
    with psycopg.connect(get_settings().conninfo) as conn:
        load_geofences(conn, read_features())


if __name__ == "__main__":
    main()
