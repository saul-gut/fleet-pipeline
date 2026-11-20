"""Cálculos geodésicos compartidos.

Las distancias se calculan sobre el elipsoide WGS84 (problema inverso de Karney, vía pyproj),
el mismo modelo que usa PostGIS con el tipo geography. Python y la base dan el mismo resultado.
"""

from pyproj import Geod

_WGS84 = Geod(ellps="WGS84")


def geodesic_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica en metros entre dos puntos en grados decimales (EPSG:4326)."""
    _, _, distance = _WGS84.inv(lon1, lat1, lon2, lat2)
    return float(distance)
