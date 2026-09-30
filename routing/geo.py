"""Small geography helpers shared by the loader and the route planner."""
import csv
import math
import re
from functools import lru_cache

from django.conf import settings

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    """Straight line distance between two points on Earth, in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def normalize_city(name):
    """Make city names comparable: 'St. Louis ' and 'Saint Louis' both become 'st louis'."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z ]", "", name)
    name = re.sub(r"\bsaint\b", "st", name)
    return re.sub(r"\s+", " ", name).strip()


@lru_cache(maxsize=1)
def city_coordinates():
    """Load data/us_cities.csv once into {(city, STATE): (lat, lon)}."""
    lookup = {}
    path = settings.BASE_DIR / "data" / "us_cities.csv"
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (normalize_city(row["CITY"]), row["STATE_CODE"].strip().upper())
            lookup.setdefault(key, (float(row["LATITUDE"]), float(row["LONGITUDE"])))
    return lookup


def lookup_city(text):
    """Turn 'Chicago, IL' into coordinates without any API call. Returns None if not found."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2:
        return None
    city, state = parts[-2], parts[-1].upper()
    return city_coordinates().get((normalize_city(city), state))