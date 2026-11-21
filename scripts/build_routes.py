"""Genera las rutas de la flota simulada con OSRM y las guarda en data/routes/routes.geojson.

Se ejecuta una sola vez (o cuando cambien los puntos de paso). El resultado se versiona en el
repositorio, así la simulación no necesita conexión a internet.

Uso: uv run python scripts/build_routes.py

Fuente: servidor público de demostración de OSRM (https://project-osrm.org) sobre datos de
OpenStreetMap (© colaboradores de OpenStreetMap, licencia ODbL).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_routes")

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "routes" / "routes.geojson"
PAUSE_BETWEEN_REQUESTS_S = 1.5  # uso respetuoso del servidor público de demostración

Point = tuple[float, float]  # (lat, lon)

# Puntos de interés (lat, lon)
BOG = {
    "corabastos": (4.6290, -74.1560),
    "chapinero": (4.6560, -74.0560),
    "usaquen": (4.6950, -74.0310),
    "puente_aranda": (4.6230, -74.1090),
    "suba": (4.7410, -74.0840),
    "aeropuerto": (4.7016, -74.1469),
    "plaza_bolivar": (4.5981, -74.0761),
    "bosa": (4.6180, -74.1880),
    "kennedy": (4.6290, -74.1500),
    "engativa": (4.7070, -74.1100),
    "meissen": (4.5600, -74.1450),
    "fontibon": (4.6730, -74.1410),
}
MED = {
    "poblado": (6.2087, -75.5665),
    "parque_berrio": (6.2518, -75.5636),
    "manrique": (6.2700, -75.5480),
    "belen": (6.2310, -75.6040),
    "laureles": (6.2450, -75.5900),
    "castilla": (6.2950, -75.5720),
    "robledo": (6.2780, -75.5960),
    "guayabal": (6.2160, -75.5850),
}
# Puntos de paso del corredor por la Autopista Medellín–Bogotá (Ruta 60), para que la ruta
# atraviese los tramos de riesgo definidos en la D-06 (El Santuario–Caño Alegre y Guarne).
CORRIDOR_VIA = [
    (5.2060, -74.7420),  # Honda
    (5.8710, -74.6410),  # Puerto Triunfo
    (6.1370, -75.2650),  # El Santuario
    (6.2780, -75.4440),  # Guarne
]


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    name: str
    kind: str  # urban_bogota | urban_medellin | interurban
    waypoints: list[Point]


ROUTES = [
    RouteSpec(
        "BOG-01",
        "Corabastos – Chapinero – Usaquén",
        "urban_bogota",
        [BOG["corabastos"], BOG["chapinero"], BOG["usaquen"]],
    ),
    RouteSpec(
        "BOG-02", "Puente Aranda – Suba", "urban_bogota", [BOG["puente_aranda"], BOG["suba"]]
    ),
    RouteSpec(
        "BOG-03",
        "Aeropuerto – Plaza de Bolívar",
        "urban_bogota",
        [BOG["aeropuerto"], BOG["plaza_bolivar"]],
    ),
    RouteSpec(
        "BOG-04",
        "Bosa – Kennedy – Engativá",
        "urban_bogota",
        [BOG["bosa"], BOG["kennedy"], BOG["engativa"]],
    ),
    RouteSpec("BOG-05", "Meissen – Fontibón", "urban_bogota", [BOG["meissen"], BOG["fontibon"]]),
    RouteSpec(
        "MED-01",
        "El Poblado – Parque Berrío – Manrique",
        "urban_medellin",
        [MED["poblado"], MED["parque_berrio"], MED["manrique"]],
    ),
    RouteSpec(
        "MED-02",
        "Belén – Laureles – Castilla",
        "urban_medellin",
        [MED["belen"], MED["laureles"], MED["castilla"]],
    ),
    RouteSpec("MED-03", "Robledo – El Poblado", "urban_medellin", [MED["robledo"], MED["poblado"]]),
    RouteSpec(
        "COR-01",
        "Bogotá (Puente Aranda) – Medellín (Guayabal)",
        "interurban",
        [BOG["puente_aranda"], *CORRIDOR_VIA, MED["guayabal"]],
    ),
]


def fetch_route(client: httpx.Client, spec: RouteSpec) -> dict:
    """Consulta OSRM y devuelve un Feature GeoJSON con la velocidad típica por tramo."""
    coords = ";".join(f"{lon},{lat}" for lat, lon in spec.waypoints)
    response = client.get(
        OSRM_URL.format(coords=coords),
        params={
            "overview": "full",
            "geometries": "geojson",
            "annotations": "distance,duration",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM respondió {payload.get('code')} para {spec.route_id}")

    route = payload["routes"][0]
    # Cada tramo (leg) entre puntos de paso trae distancia y duración por segmento.
    speeds_kmh: list[float] = []
    for leg in route["legs"]:
        for distance, duration in zip(
            leg["annotation"]["distance"], leg["annotation"]["duration"], strict=True
        ):
            speeds_kmh.append(round(distance / duration * 3.6, 1) if duration > 0 else 0.0)

    coordinates = [[round(lon, 6), round(lat, 6)] for lon, lat in route["geometry"]["coordinates"]]
    # Entre tramos OSRM repite el punto de paso: la geometría tiene un vértice más por tramo
    # que la suma de segmentos. Se eliminan vértices duplicados consecutivos y sus segmentos.
    coordinates, speeds_kmh = _drop_zero_length_segments(coordinates, speeds_kmh)

    return {
        "type": "Feature",
        "properties": {
            "route_id": spec.route_id,
            "name": spec.name,
            "kind": spec.kind,
            "distance_m": round(route["distance"], 1),
            "duration_s": round(route["duration"], 1),
            "segment_speeds_kmh": speeds_kmh,
        },
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _drop_zero_length_segments(
    coordinates: list[list[float]], speeds: list[float]
) -> tuple[list[list[float]], list[float]]:
    if len(speeds) != len(coordinates) - 1:
        raise RuntimeError(
            f"Geometría y velocidades no cuadran: {len(coordinates)} vértices, "
            f"{len(speeds)} segmentos"
        )
    clean_coords = [coordinates[0]]
    clean_speeds: list[float] = []
    for coord, speed in zip(coordinates[1:], speeds, strict=True):
        if coord != clean_coords[-1]:
            clean_coords.append(coord)
            clean_speeds.append(speed)
    return clean_coords, clean_speeds


def main() -> None:
    features = []
    with httpx.Client(timeout=60, headers={"User-Agent": "fleet-pipeline-route-builder"}) as client:
        for spec in ROUTES:
            feature = fetch_route(client, spec)
            props = feature["properties"]
            log.info(
                "%s %s: %.1f km, %d vértices",
                spec.route_id,
                spec.name,
                props["distance_m"] / 1000,
                len(feature["geometry"]["coordinates"]),
            )
            features.append(feature)
            time.sleep(PAUSE_BETWEEN_REQUESTS_S)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT.write_text(json.dumps(collection, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Guardado %s (%.0f KB)", OUTPUT, OUTPUT.stat().st_size / 1024)


if __name__ == "__main__":
    main()
