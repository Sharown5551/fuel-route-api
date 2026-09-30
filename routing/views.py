from urllib.parse import urlencode

import requests
from django.shortcuts import render
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import RouteError, plan_trip


class TripRequestSerializer(serializers.Serializer):
    start = serializers.CharField(help_text="e.g. 'Chicago, IL' or a full US address")
    finish = serializers.CharField(help_text="e.g. 'Denver, CO' or a full US address")


def run_trip(data):
    """Validate the input and plan the trip. Returns (result, error, http_status)."""
    serializer = TripRequestSerializer(data=data)
    if not serializer.is_valid():
        return None, serializer.errors, status.HTTP_400_BAD_REQUEST
    try:
        return plan_trip(**serializer.validated_data), None, status.HTTP_200_OK
    except RouteError as e:
        return None, {"error": str(e)}, status.HTTP_400_BAD_REQUEST
    except requests.RequestException:
        return None, {"error": "The map service is unavailable, try again shortly"}, status.HTTP_502_BAD_GATEWAY


class RouteView(APIView):
    """POST {"start": "...", "finish": "..."} and get the route, fuel stops and total cost."""

    def post(self, request):
        result, error, code = run_trip(request.data)
        if error:
            return Response(error, status=code)
        query = urlencode({"start": result["start"]["query"], "finish": result["finish"]["query"]})
        result = {**result, "map_url": request.build_absolute_uri(f"/api/route/map/?{query}")}
        return Response(result)


def route_map(request):
    """GET /api/route/map/?start=...&finish=...  shows the route and stops on a map."""
    result, error, code = run_trip(request.GET)
    return render(request, "routing/map.html", {"trip": result, "error": error}, status=code)