# Fuel Route API

A Django API that takes a start and finish location in the USA and returns the driving route, the best places to buy fuel along the way, and the total fuel cost.

Vehicle rules from the brief: 500 mile range, 10 miles per gallon. Fuel prices come from the provided CSV.

## How it works

1. **Locations.** "City, ST" inputs (for example `Chicago, IL`) are resolved locally from a US cities dataset, so they need no API call. Full addresses fall back to one OpenRouteService geocoding call.
2. **Route.** One call to the OpenRouteService directions API returns the route line and road distance.
3. **Stations near the route.** Each fuel station was placed at its city's coordinates when the data was loaded. Route points are indexed in a small grid, so matching around 6,600 stations to a route takes a fraction of a second. Stations within 15 miles of the route are kept, together with how far along the route they sit.
4. **Fuel plan.** From each stop, the planner looks at stations between 150 and 500 miles ahead and heads for the cheapest one. If that station is cheaper than the current one, it buys just enough fuel to reach it. If not, it fills the tank here because this is the better price. This keeps the cost close to the absolute minimum while avoiding lots of tiny top ups.
5. **Caching.** Results are cached for an hour, so repeating a request makes no external calls at all.

Result: a trip needs **one** routing API call when inputs are "City, ST", and at most three when full addresses are used.

## Assumptions

* The vehicle leaves the start with just enough fuel to reach its first stop (the cheapest station within 25 miles of the start) and arrives at the finish with an empty tank.
* A station's location is its city's centre. That is accurate enough for choosing fuel stops along a highway.
* Stations outside the USA, or in cities that could not be matched (under 0.2% of US rows), are skipped.

## Setup

```
python -m venv venv
venv\Scripts\activate          (Windows)   or   source venv/bin/activate   (Mac/Linux)
pip install -r requirements.txt
```

Create a `.env` file in the project folder with a free key from https://openrouteservice.org:

```
ORS_API_KEY=your_key_here
```

Then create the database and load the stations:

```
python manage.py migrate
python manage.py load_stations
python manage.py runserver
```

## Usage

**POST** `/api/route/`

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

Response (shortened):

```json
{
  "distance_miles": 2795.8,
  "total_gallons": 278.98,
  "total_fuel_cost": 850.61,
  "fuel_stops": [
    {
      "name": "AKAL TRAVEL CENTER",
      "city": "Waco",
      "state": "NE",
      "route_mile": 1334.4,
      "price_per_gallon": 2.799,
      "gallons": 50.0,
      "cost": 139.95
    }
  ],
  "route": { "type": "LineString", "coordinates": [[-73.9965, 40.7486], "..."] },
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA"
}
```

**GET** `/api/route/map/?start=Chicago, IL&finish=Denver, CO` shows the route and fuel stops on an interactive map.

## Project structure

```
routing/
  geo.py                            distance maths and city lookup
  services.py                       geocoding, routing, station matching, fuel planning
  views.py                          API endpoint and map page
  management/commands/load_stations.py   loads the fuel price CSV
  templates/routing/map.html        Leaflet map
data/
  fuel_prices.csv                   provided fuel prices
  us_cities.csv                     US city coordinates
```