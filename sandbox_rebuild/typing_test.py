def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


if __name__ == "__main__":
    print(round(haversine_km(-1.2864, 36.8172, -0.0917, 34.7680), 1))