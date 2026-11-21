"""Rutas de la flota (generadas con scripts/build_routes.py) y posición a lo largo de ellas."""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from itertools import accumulate
from pathlib import Path

from fleet_pipeline.geo import geodesic_distance_m

DEFAULT_ROUTES_PATH = Path(__file__).resolve().parents[3] / "data" / "routes" / "routes.geojson"


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    road_speed_kmh: float  # velocidad típica de la vía según OSRM (flujo libre)


@dataclass(frozen=True)
class Route:
    route_id: str
    name: str
    kind: str  # urban_bogota | urban_medellin | interurban
    coords: tuple[tuple[float, float], ...]  # (lat, lon)
    segment_speeds_kmh: tuple[float, ...]
    cumulative_m: tuple[float, ...]  # distancia acumulada en cada vértice; empieza en 0

    @property
    def length_m(self) -> float:
        return self.cumulative_m[-1]

    @property
    def is_urban(self) -> bool:
        return self.kind != "interurban"

    def point_at(self, distance_m: float) -> RoutePoint:
        """Posición a `distance_m` metros del inicio, interpolada dentro del segmento."""
        distance_m = min(max(distance_m, 0.0), self.length_m)
        segment = min(bisect_right(self.cumulative_m, distance_m) - 1, len(self.coords) - 2)
        start, end = self.cumulative_m[segment], self.cumulative_m[segment + 1]
        fraction = (distance_m - start) / (end - start) if end > start else 0.0
        (lat1, lon1), (lat2, lon2) = self.coords[segment], self.coords[segment + 1]
        return RoutePoint(
            lat=lat1 + (lat2 - lat1) * fraction,
            lon=lon1 + (lon2 - lon1) * fraction,
            road_speed_kmh=self.segment_speeds_kmh[segment],
        )


def build_route(
    route_id: str,
    name: str,
    kind: str,
    coords: list[tuple[float, float]],
    segment_speeds_kmh: list[float],
) -> Route:
    """Crea una ruta calculando las distancias geodésicas acumuladas."""
    if len(coords) < 2 or len(segment_speeds_kmh) != len(coords) - 1:
        raise ValueError(f"Ruta {route_id}: se esperan N vértices y N-1 velocidades")
    segment_lengths = (
        geodesic_distance_m(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1)
    )
    return Route(
        route_id=route_id,
        name=name,
        kind=kind,
        coords=tuple(coords),
        segment_speeds_kmh=tuple(segment_speeds_kmh),
        cumulative_m=tuple(accumulate(segment_lengths, initial=0.0)),
    )


def load_routes(path: Path = DEFAULT_ROUTES_PATH) -> dict[str, Route]:
    """Lee el GeoJSON de rutas. Las coordenadas GeoJSON vienen como [lon, lat]."""
    collection = json.loads(path.read_text(encoding="utf-8"))
    routes = {}
    for feature in collection["features"]:
        props = feature["properties"]
        coords = [(lat, lon) for lon, lat in feature["geometry"]["coordinates"]]
        routes[props["route_id"]] = build_route(
            props["route_id"], props["name"], props["kind"], coords, props["segment_speeds_kmh"]
        )
    return routes
