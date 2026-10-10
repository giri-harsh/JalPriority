"""Synthetic geometry helpers: haversine, road distance (haversine x 1.3), local km offsets."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0
ROAD_FACTOR = 1.3
KM_PER_DEG_LAT = 111.32


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def road_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Synthetic road distance: haversine x ROAD_FACTOR, rounded to 2 dp (CONTRACT 4.4)."""
    return round(ROAD_FACTOR * haversine_km(lat1, lon1, lat2, lon2), 2)


def offset_latlon(lat0: float, lon0: float, east_km: float, north_km: float) -> tuple[float, float]:
    """Point ``east_km`` / ``north_km`` from (lat0, lon0) on a local flat projection, rounded to 4 dp."""
    lat = lat0 + north_km / KM_PER_DEG_LAT
    lon = lon0 + east_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return round(lat, 4), round(lon, 4)


def distance_matrix(nodes: list[tuple[str, float, float]]) -> dict[str, dict[str, float]]:
    """Full symmetric road-distance matrix over ``(node_id, lat, lon)``; diagonal 0.0, keys in input order."""
    matrix: dict[str, dict[str, float]] = {nid: {} for nid, _, _ in nodes}
    for i, (a, lat_a, lon_a) in enumerate(nodes):
        matrix[a][a] = 0.0
        for b, lat_b, lon_b in nodes[i + 1 :]:
            d = road_km(lat_a, lon_a, lat_b, lon_b)
            matrix[a][b] = d
            matrix[b][a] = d
    # Re-key each row in input order so the JSON layout is stable and readable.
    order = [nid for nid, _, _ in nodes]
    return {a: {b: matrix[a][b] for b in order} for a in order}
