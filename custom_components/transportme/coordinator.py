"""DataUpdateCoordinator for TransportMe Bus Tracker (GraphQL API)."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_BASE_URL,
    CONF_AUTH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SUBSCRIPTION_ID,
    CONF_STOP_LAT,
    CONF_STOP_LON,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL      = "https://production.api2.transportme.com.au/"
# Public client-side key embedded in the TransportMe app bundle (safe to publish)
FIREBASE_API_KEY = "AIzaSyD9xVRwjC0V-FHj5D97pwD8oGUNCufs9vI"
TOKEN_REFRESH_URL = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"

# ---------------------------------------------------------------------------
# GraphQL queries (exact schema from app bundle)
# ---------------------------------------------------------------------------

QUERY_TRACKING_ROUTES = """
query trackingRoutes {
    trackingRoutes {
        id
        name
        trackable_routes {
            id
            name
            trackable_stops {
                id
                display_name
                departure_time
                position
                name
                address
                lat
                lng
                route_stop_id
                tracked
            }
        }
    }
}
"""

QUERY_RUNNING_ROUTES = """
query runningRoutes($routes: [inputRunningRoutes!]) {
    runningRoutes(routes: $routes) {
        dbr_id
        operator_id
        route_id
        route_name
        bus_id
        bus_number
        lat
        lng
        pax_app_show_pax_count
    }
}
"""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class TransportMeCoordinator(DataUpdateCoordinator):
    """Polls TransportMe GraphQL API and surfaces data to HA entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_data: dict,
        update_interval: int,
    ) -> None:
        self._url           = config_entry_data.get(CONF_API_BASE_URL, GRAPHQL_URL)
        self._token         = config_entry_data.get(CONF_AUTH_TOKEN, "")
        self._refresh_token = config_entry_data.get(CONF_REFRESH_TOKEN, "")
        self._subscription_id = config_entry_data.get(CONF_SUBSCRIPTION_ID, "")
        self._stop_lat      = config_entry_data.get(CONF_STOP_LAT)
        self._stop_lon      = config_entry_data.get(CONF_STOP_LON)

        # Cached route list from trackingRoutes query
        self._routes: list[dict] | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    async def _gql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        async with session.post(
            self._url,
            json=payload,
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (401, 403):
                raise _TokenExpiredError()
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            if "errors" in data:
                err_msg = data["errors"][0].get("message", "unknown")
                if "token" in err_msg.lower() or data["errors"][0].get("extensions", {}).get("code") == "UNAUTHENTICATED":
                    raise _TokenExpiredError()
                raise UpdateFailed(f"GraphQL error: {err_msg}")
            return data

    # ------------------------------------------------------------------
    # Token refresh (Firebase Secure Token API)
    # ------------------------------------------------------------------

    async def _refresh_id_token(self) -> None:
        if not self._refresh_token:
            raise UpdateFailed(
                "Auth token expired and no refresh token stored. "
                "Go to Settings → Devices & Services → TransportMe → Configure "
                "and tick 'Sign in again' to re-authenticate."
            )
        session = async_get_clientsession(self.hass)
        async with session.post(
            TOKEN_REFRESH_URL,
            json={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"Token refresh failed: HTTP {resp.status}")
            body = await resp.json(content_type=None)
            new_token = body.get("id_token")
            if not new_token:
                raise UpdateFailed("Token refresh response missing id_token")
            self._token = new_token
            _LOGGER.info("TransportMe: Firebase token auto-refreshed successfully.")

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    async def _fetch_tracking_routes(self) -> list[dict]:
        """Get the list of routes the user is subscribed to track."""
        result = await self._gql(QUERY_TRACKING_ROUTES)
        return result.get("data", {}).get("trackingRoutes", [])

    async def _fetch_running_routes(self, routes_input: list[dict]) -> list[dict]:
        """Get live vehicle positions for the given routes."""
        result = await self._gql(
            QUERY_RUNNING_ROUTES,
            variables={"routes": routes_input},
        )
        return result.get("data", {}).get("runningRoutes", [])

    async def _fetch_with_refresh(self) -> dict[str, Any]:
        """Fetch data, transparently refreshing the token if needed."""
        try:
            return await self._do_fetch()
        except _TokenExpiredError:
            _LOGGER.debug("TransportMe: token expired, attempting refresh.")
            await self._refresh_id_token()
            return await self._do_fetch()

    async def _do_fetch(self) -> dict[str, Any]:
        # Build routes input from configured operator_id + route_ids
        # operator_id is stored in CONF_SUBSCRIPTION_ID as "operator_id:route_id,route_id,..."
        # e.g. "123:1,2,3"
        routes_input: list[dict] = []
        route_meta: dict[str, dict] = {}

        raw_sub = self._subscription_id  # e.g. "123:1,2,3"
        if ":" in raw_sub:
            op_id_str, route_ids_str = raw_sub.split(":", 1)
            op_id = int(op_id_str.strip())
            for rid_str in route_ids_str.split(","):
                rid_str = rid_str.strip()
                if rid_str:
                    rid = int(rid_str)
                    routes_input.append({"operator_id": op_id, "route_id": rid})
                    route_meta[f"{op_id}_{rid}"] = {"operator_id": op_id, "route_id": rid}
        else:
            # Legacy: single operator id, fetch all routes via canBeTrackRoutes
            op_id = int(raw_sub)
            result = await self._gql(
                "query canBeTrackRoutes($fav_operator_id: Int!) { canBeTrackRoutes(fav_operator_id: $fav_operator_id) { id name } }",
                {"fav_operator_id": op_id}
            )
            for route in result.get("data", {}).get("canBeTrackRoutes", []):
                routes_input.append({"operator_id": op_id, "route_id": route["id"]})
                route_meta[f"{op_id}_{route['id']}"] = {"route_name": route.get("name")}

        if not routes_input:
            raise UpdateFailed("No trackable routes found. Check your Subscription ID setting.")

        # Step 2 – get live positions
        running = await self._fetch_running_routes(routes_input)

        if not running:
            # Bus not running yet – return empty positions but don't error
            return {"vehicles": [], "route_meta": route_meta, "subscription_id": self._subscription_id}

        return {"vehicles": running, "route_meta": route_meta, "subscription_id": self._subscription_id}

    # ------------------------------------------------------------------
    # Data normalisation
    # ------------------------------------------------------------------

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any]:
        vehicles = raw.get("vehicles", [])
        subscription_id = raw.get("subscription_id", "")

        if not vehicles:
            return {
                "latitude":    None,
                "longitude":   None,
                "speed":       None,
                "heading":     None,
                "status":      "not_running",
                "vehicle_id":  None,
                "bus_number":  None,
                "route_name":  None,
                "eta_minutes": None,
                "distance_km": None,
                "all_vehicles": [],
            }

        # If subscription_id matches a route_id, use that vehicle; else use first
        vehicle = vehicles[0]
        for v in vehicles:
            if str(v.get("route_id")) == str(subscription_id) or \
               str(v.get("dbr_id"))   == str(subscription_id):
                vehicle = v
                break

        lat = vehicle.get("lat")
        lon = vehicle.get("lng")
        distance_km: float | None = None
        eta_minutes: float | None = None

        if lat is not None and lon is not None and self._stop_lat and self._stop_lon:
            distance_km = round(_haversine_km(float(lat), float(lon), self._stop_lat, self._stop_lon), 2)

        return {
            "latitude":     float(lat) if lat is not None else None,
            "longitude":    float(lon) if lon is not None else None,
            "speed":        None,   # not in runningRoutes response
            "heading":      None,
            "status":       "running",
            "vehicle_id":   str(vehicle.get("bus_id", "")),
            "bus_number":   str(vehicle.get("bus_number", "")),
            "route_name":   vehicle.get("route_name"),
            "operator_id":  vehicle.get("operator_id"),
            "route_id":     vehicle.get("route_id"),
            "pax_count":    vehicle.get("pax_app_show_pax_count"),
            "eta_minutes":  eta_minutes,
            "distance_km":  distance_km,
            "all_vehicles": vehicles,
        }

    # ------------------------------------------------------------------
    # Coordinator entry point
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        raw = await self._fetch_with_refresh()
        return self._normalise(raw)


class _TokenExpiredError(Exception):
    """Raised internally when the API signals auth failure."""
