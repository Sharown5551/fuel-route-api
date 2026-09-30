"""Route planning: geocoding, routing and choosing the cheapest fuel stops."""
import hashlib
from functools import lru_cache

import requests
from django.conf import settings
from django.core.cache import cache

from .geo import haversine_miles, lookup_city
from .models import FuelStation

ORS_BASE = "https://api.openrouteservice.org"
METERS_PER_MILE = 1609.344
MAX_DETOUR_MILES = 15      # how far a station may be from the route
START_WINDOW_MILES = 25    # the vehicle fuels up within this distance of the start
MIN_LEG_MILES = 150        # avoid tiny top ups: next stop is normally at least this far ahead
MAX_MAP_POINTS = 1500      # route points returned in the response (keeps it small and fast)
GRID_SIZE = 0.25           # degrees, for the fast nearest point search


class RouteError(Exception):
    """A problem the user should see, e.g. an unknown location."""


# 1. Locations -----------------------------------------------------------

def geocode(text):
    """Return (lat, lon) for a location inside the USA.

    'City, ST' is looked up locally with no API call.
    Anything else (a full address, a landmark) uses one ORS geocoding call.
    """
    coords = lookup_city(text)
    if coords:
        return coords

    response = requests.get(
        f"{ORS_BASE}/geocode/search",
        params={"api_key": settings.ORS_API_KEY, "text": text,
                "boundary.country": "US", "size": 1},
        timeout=15,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        raise RouteError(f"Could not find a location in the USA for '{text}'")
    lon, lat = features[0]["geometry"]["coordinates"]
    return lat, lon


# 2. Route ----------------------------------------------------------------

def get_route(start, finish):
    """One call to ORS directions. Returns (list of (lat, lon) points, distance in miles)."""
    response = requests.post(
        f"{ORS_BASE}/v2/directions/driving-car/geojson",
        json={"coordinates": [[start[1], start[0]], [finish[1], finish[0]]]},
        headers={"Authorization": settings.ORS_API_KEY},
        timeout=30,
    )
    if response.status_code != 200:
        raise RouteError(f"Routing service error: {response.text[:200]}")
    feature = response.json()["features"][0]
    points = [(lat, lon) for lon, lat in feature["geometry"]["coordinates"]]
    miles = feature["properties"]["summary"]["distance"] / METERS_PER_MILE
    return points, miles


# 3. Stations along the route -----------------------------------------------

@lru_cache(maxsize=1)
def all_stations():
    """Read the stations table once and keep it in memory."""
    return list(FuelStation.objects.values(
        "id", "name", "address", "city", "state", "price", "latitude", "longitude"))


def cumulative_miles(points):
    """Distance from the start to every point on the route."""
    totals = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        totals.append(totals[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    return totals


def stations_along_route(points, route_miles):
    """Find stations near the route and how far along the route each one is.

    Route points are placed in a grid so each station only checks the few
    points close to it instead of the whole route.
    """
    along = cumulative_miles(points)
    scale = route_miles / along[-1] if along[-1] else 1  # match the road distance from ORS

    grid = {}
    for i, (lat, lon) in enumerate(points):
        grid.setdefault((int(lat // GRID_SIZE), int(lon // GRID_SIZE)), []).append(i)

    found = []
    for s in all_stations():
        cell_lat, cell_lon = int(s["latitude"] // GRID_SIZE), int(s["longitude"] // GRID_SIZE)
        best = None
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for i in grid.get((cell_lat + dlat, cell_lon + dlon), ()):
                    d = haversine_miles(s["latitude"], s["longitude"], *points[i])
                    if best is None or d < best[0]:
                        best = (d, i)
        if best and best[0] <= MAX_DETOUR_MILES:
            found.append({**s, "price": float(s["price"]),
                          "route_mile": along[best[1]] * scale,
                          "detour_miles": best[0]})

    found.sort(key=lambda s: s["route_mile"])
    return found


# 4. Choosing fuel stops ------------------------------------------------------

def plan_fuel_stops(stations, route_miles, max_range, mpg):
    """Pick fuel stops that keep the trip cheap without stopping too often.

    Assumption: the vehicle leaves the start with just enough fuel to reach its
    first stop, the cheapest station within the first START_WINDOW_MILES.
    From there it never runs dry and arrives at the finish with an empty tank.

    At each stop we look at the stations between MIN_LEG_MILES and the full
    range ahead and head for the cheapest one:
      * if it is cheaper than here, buy just enough fuel to reach it;
      * if it is not, fill the tank here, since this is the better price.
    When the finish is within range we buy just enough to finish, unless a
    cheaper station is still ahead.
    """
    first = [s for s in stations if s["route_mile"] <= START_WINDOW_MILES]
    if not first:
        raise RouteError("No fuel station found near the start of the route")

    current = min(first, key=lambda s: s["price"])
    position = current["route_mile"]
    fuel_miles = 0.0          # miles we can still drive with the fuel in the tank
    stops = []

    while True:
        remaining = route_miles - position
        min_next = position + min(MIN_LEG_MILES, max_range)

        if remaining <= max_range:
            cheaper = [s for s in stations
                       if min_next <= s["route_mile"] < route_miles and s["price"] < current["price"]]
            target = min(cheaper, key=lambda s: s["price"]) if cheaper else None
        else:
            window = [s for s in stations if min_next <= s["route_mile"] <= position + max_range]
            if not window:  # no station far enough ahead, accept a shorter hop
                window = [s for s in stations if position < s["route_mile"] <= position + max_range]
            if not window:
                raise RouteError("A stretch of this route has no fuel station within range")
            target = min(window, key=lambda s: (s["price"], -s["route_mile"]))

        next_position = target["route_mile"] if target else route_miles
        leg = next_position - position
        if target and target["price"] >= current["price"]:
            buy_miles = max_range - fuel_miles           # this is the better price: fill up
        else:
            buy_miles = max(0.0, leg - fuel_miles)       # buy just enough for the next leg

        if buy_miles > 0:
            gallons = buy_miles / mpg
            stops.append({
                "name": current["name"],
                "address": current["address"],
                "city": current["city"],
                "state": current["state"],
                "latitude": current["latitude"],
                "longitude": current["longitude"],
                "route_mile": round(position, 1),
                "price_per_gallon": round(current["price"], 3),
                "gallons": round(gallons, 2),
                "cost": round(gallons * current["price"], 2),
            })

        fuel_miles += buy_miles - leg
        if target is None:
            break
        current, position = target, next_position

    total_cost = round(sum(s["cost"] for s in stops), 2)
    return stops, total_cost


# 5. Everything together ------------------------------------------------------

def simplify(points):
    """Keep about MAX_MAP_POINTS route points for the response, always including the last one."""
    step = max(1, len(points) // MAX_MAP_POINTS)
    kept = points[::step]
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return [[round(lon, 5), round(lat, 5)] for lat, lon in kept]


def plan_trip(start, finish):
    """Full result for a trip. Results are cached so repeated requests make no API calls."""
    trip_id = f"{start.lower().strip()}|{finish.lower().strip()}"
    cache_key = "trip:" + hashlib.md5(trip_id.encode()).hexdigest()
    result = cache.get(cache_key)
    if result:
        return result

    start_point, finish_point = geocode(start), geocode(finish)
    points, route_miles = get_route(start_point, finish_point)
    stations = stations_along_route(points, route_miles)
    stops, total_cost = plan_fuel_stops(
        stations, route_miles, settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG)

    result = {
        "start": {"query": start, "latitude": start_point[0], "longitude": start_point[1]},
        "finish": {"query": finish, "latitude": finish_point[0], "longitude": finish_point[1]},
        "distance_miles": round(route_miles, 1),
        "total_gallons": round(sum(s["gallons"] for s in stops), 2),
        "total_fuel_cost": total_cost,
        "fuel_stops": stops,
        "route": {"type": "LineString", "coordinates": simplify(points)},
    }
    cache.set(cache_key, result, 60 * 60)
    return result