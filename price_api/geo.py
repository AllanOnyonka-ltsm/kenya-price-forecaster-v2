"""Pure geographic helpers (stdlib only) so they can be unit-tested without loading the model."""

import math
from typing import Dict, List, Mapping, Optional, Tuple

EARTH_RADIUS_KM = 6371.0088  # mean Earth radius (IUGG)

Coords = Tuple[float, float]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def markets_within_radius(
    origin: str,
    coords: Mapping[str, Coords],
    radius_km: float,
    limit: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Markets (excluding `origin`) whose haversine distance to `origin` is <= radius_km.

    Returns [{"market": name, "distance_km": float (1 dp)}, ...] sorted nearest first
    (ties broken by name). The radius test uses the unrounded distance.
    Raises KeyError if origin is not in coords, ValueError if radius_km is not a
    finite non-negative number.
    """
    if origin not in coords:
        raise KeyError(origin)
    if radius_km is None or not math.isfinite(radius_km) or radius_km < 0:
        raise ValueError(f"radius_km must be a finite number >= 0, got {radius_km!r}")
    olat, olon = coords[origin]
    found = []
    for name, (lat, lon) in coords.items():
        if name == origin:
            continue
        d = haversine_km(olat, olon, lat, lon)
        if d <= radius_km:
            found.append((d, name))
    found.sort()
    if limit is not None:
        found = found[: max(limit, 0)]
    return [{"market": name, "distance_km": round(d, 1)} for d, name in found]


# Kenya's extent with a small margin. An override outside it is almost certainly a typo
# (swapped lat/lon, missing minus sign).
KENYA_BBOX = {"lat": (-5.0, 5.5), "lon": (33.5, 42.5)}


def apply_coord_overrides(
    coords: Mapping[str, Coords],
    overrides: Mapping[str, Mapping[str, object]],
) -> Dict[str, Coords]:
    """Return a copy of `coords` with manual corrections applied.

    Each override is {"lat": float, "lon": float, "source": str, ...}. The source is required:
    a coordinate is only replaced when someone can say where the new value comes from.
    Raises ValueError for an unknown market, a missing source, or a non-finite or
    out-of-Kenya coordinate, so a bad override fails loudly at startup instead of
    silently moving a market.
    """
    out = dict(coords)
    for name, o in overrides.items():
        if name not in coords:
            raise ValueError(f"coordinate override for unknown market {name!r}")
        source = o.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"coordinate override for {name!r} has no source")
        try:
            lat, lon = float(o["lat"]), float(o["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"coordinate override for {name!r} needs numeric lat and lon")
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError(f"coordinate override for {name!r} is not finite")
        (la0, la1), (lo0, lo1) = KENYA_BBOX["lat"], KENYA_BBOX["lon"]
        if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
            raise ValueError(f"coordinate override for {name!r} is outside Kenya: ({lat}, {lon})")
        out[name] = (lat, lon)
    return out
