"""Load fuel stations from data/fuel_prices.csv into the database.

Run once:  python manage.py load_stations

Each station is placed at the coordinates of its city (from data/us_cities.csv),
so no geocoding API is needed. Stations outside the USA or in cities that
cannot be matched are skipped.
"""
import csv

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.geo import city_coordinates, normalize_city
from routing.models import FuelStation


class Command(BaseCommand):
    help = "Load fuel stations and their coordinates into the database"

    def handle(self, *args, **options):
        cities = city_coordinates()
        path = settings.BASE_DIR / "data" / "fuel_prices.csv"

        stations = {}
        skipped = 0
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                state = row["State"].strip().upper()
                coords = cities.get((normalize_city(row["City"]), state))
                if coords is None:
                    skipped += 1
                    continue

                opis_id = int(row["OPIS Truckstop ID"])
                price = float(row["Retail Price"])
                # The file lists some stations more than once; keep the cheapest price.
                if opis_id in stations and stations[opis_id].price <= price:
                    continue

                stations[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=state,
                    price=price,
                    latitude=coords[0],
                    longitude=coords[1],
                )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)} stations ({skipped} rows skipped: outside USA or unknown city)"
        ))