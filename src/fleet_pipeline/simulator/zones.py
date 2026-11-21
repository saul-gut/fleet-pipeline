"""Límite de velocidad de cada tramo de una ruta según las geocercas (D-18).

Se calcula una sola vez por ruta (en el punto medio de cada segmento) con la misma regla de
la capa Plata: la geocerca más restrictiva. Así el simulador no hace un punto en polígono en
cada paso, y el conductor "sabe" qué límite rige donde va.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape

from fleet_pipeline.geofences.loader import DEFAULT_GEOFENCES_PATH
from fleet_pipeline.processing.rules import ZoneLimit, applied_zone
from fleet_pipeline.simulator.routes import Route


class ZoneIndex:
    """Índice espacial (STRtree) de las geocercas con su límite."""

    def __init__(self, path: Path = DEFAULT_GEOFENCES_PATH) -> None:
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        self._geoms = [shape(f["geometry"]) for f in features]
        self._limits = [
            ZoneLimit(
                f["properties"]["code"],
                f["properties"]["zone_type"],
                f["properties"]["speed_limit_kmh"],
            )
            for f in features
        ]
        self._tree = STRtree(self._geoms)

    def limit_at(self, lat: float, lon: float) -> int | None:
        """Límite de la geocerca más restrictiva que contiene el punto (None si ninguna)."""
        hits = self._tree.query(Point(lon, lat), predicate="within")
        zone = applied_zone([self._limits[i] for i in hits])
        return zone.speed_limit_kmh if zone else None


def with_zone_limits(route: Route, index: ZoneIndex) -> Route:
    """Devuelve la ruta con el límite aplicable en cada segmento."""
    limits = tuple(
        index.limit_at((lat1 + lat2) / 2, (lon1 + lon2) / 2)
        for (lat1, lon1), (lat2, lon2) in zip(route.coords, route.coords[1:], strict=False)
    )
    return replace(route, segment_zone_limits=limits)


def load_routes_with_zones(
    routes: dict[str, Route], index: ZoneIndex | None = None
) -> dict[str, Route]:
    index = index or ZoneIndex()
    return {route_id: with_zone_limits(route, index) for route_id, route in routes.items()}
