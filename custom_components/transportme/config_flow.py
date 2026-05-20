"""Config flow for TransportMe Bus Tracker.

Authentication uses a HA-hosted sign-in page opened via a link in the config
flow form.  Tokens are stored in hass.data by the callback view; the flow step
picks them up when the user clicks Submit.  This avoids async_configure on an
EXTERNAL_STEP flow (which is not supported in modern HA).

Config flow steps
-----------------
  user    – form with sign-in link; callback stores tokens; Submit picks them up
  routes  – subscription ID (auto-discovered), stop coords, poll interval

Options flow steps
------------------
  init    – pre-filled settings + "Sign in again" toggle
  reauth  – same form-with-link pattern for re-auth
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_API_BASE_URL,
    CONF_AUTH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_STOP_LAT,
    CONF_STOP_LON,
    CONF_SUBSCRIPTION_ID,
    DEFAULT_API_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .views import verify_transportme_token

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL = "https://production.api2.transportme.com.au/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ha_url(hass) -> str:
    """Return the internal HA base URL best suited for the local browser."""
    try:
        from homeassistant.helpers.network import get_url
        return get_url(hass, prefer_external=False)
    except Exception:
        return ""


def _pending_key(flow_id: str) -> str:
    """hass.data key where the callback view stores auth tokens for a flow."""
    return f"{DOMAIN}_pending_{flow_id}"


async def _discover_subscription_id(id_token: str) -> str:
    """
    Auto-discover the user's tracked routes via the trackingRoutes query.
    Returns "operator_id:route_id,route_id,..." or "" on any failure.
    The trackable_stops sub-field is intentionally omitted to avoid a
    known server-side SQL bug on that field.
    """
    query = """
    query trackingRoutes {
        trackingRoutes {
            id
            name
            trackable_routes { id name }
        }
    }
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GRAPHQL_URL,
                json={"query": query},
                headers={
                    "Authorization": f"Bearer {id_token}",
                    "Content-Type":  "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return ""
                body = await resp.json(content_type=None)
                if "errors" in body:
                    _LOGGER.debug("trackingRoutes errors: %s", body["errors"])
                    return ""
                tracking = body.get("data", {}).get("trackingRoutes", [])
                if not tracking:
                    return ""
                op = tracking[0]
                op_id = op.get("id")
                route_ids = [
                    str(r["id"])
                    for r in op.get("trackable_routes", [])
                    if r.get("id") is not None
                ]
                if not op_id or not route_ids:
                    return ""
                return f"{op_id}:{','.join(route_ids)}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class TransportMeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: sign-in link → route settings."""

    VERSION = 1

    def __init__(self) -> None:
        self._id_token:       str = ""
        self._refresh_token:  str = ""
        self._pax_user:       dict | None = None
        self._discovered_sub: str = ""

    # ------------------------------------------------------------------
    # View registration helper
    # ------------------------------------------------------------------

    def _ensure_views(self) -> None:
        """Register HTTP views if not already done."""
        key = f"{DOMAIN}_views_registered"
        if self.hass.data.get(key):
            return
        from .views import TransportMeAuthView, TransportMeCallbackView
        self.hass.http.register_view(TransportMeAuthView())
        self.hass.http.register_view(TransportMeCallbackView())
        self.hass.data[key] = True

    # ------------------------------------------------------------------
    # Step 1 – Sign-in form with link
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        First call (user_input=None): show form with sign-in link.
        Second call (Submit clicked): pick up tokens stored by callback view.
        """
        self._ensure_views()
        errors: dict[str, str] = {}

        if user_input is not None:
            # User clicked Submit — check for tokens stored by the callback view
            auth_data = self.hass.data.pop(_pending_key(self.flow_id), None)
            if not auth_data:
                errors["base"] = "auth_not_completed"
            else:
                self._id_token       = auth_data["id_token"]
                self._refresh_token  = auth_data["refresh_token"]
                self._pax_user       = await verify_transportme_token(self._id_token)
                self._discovered_sub = await _discover_subscription_id(self._id_token)
                return await self.async_step_routes()

        # Reserve slot in hass.data (callback view writes here after sign-in)
        key = _pending_key(self.flow_id)
        if key not in self.hass.data:
            self.hass.data[key] = None

        base     = _ha_url(self.hass)
        auth_url = f"{base}/api/transportme/auth?flow_id={self.flow_id}&flow_type=config"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"auth_url": auth_url},
        )

    # ------------------------------------------------------------------
    # Step 2 – Route settings (auto-populated when possible)
    # ------------------------------------------------------------------

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        default_sub = self._discovered_sub
        if not default_sub and self._pax_user and self._pax_user.get("fav_operator_id"):
            default_sub = f"{self._pax_user['fav_operator_id']}:"

        if user_input is not None:
            sub_id = user_input[CONF_SUBSCRIPTION_ID].strip()
            if not sub_id or ":" not in sub_id:
                errors[CONF_SUBSCRIPTION_ID] = "invalid_sub_id"
            if not errors:
                await self.async_set_unique_id(f"{DOMAIN}_{sub_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"TransportMe – {sub_id}",
                    data={
                        CONF_API_BASE_URL:    DEFAULT_API_BASE_URL,
                        CONF_AUTH_TOKEN:      self._id_token,
                        CONF_REFRESH_TOKEN:   self._refresh_token,
                        CONF_SUBSCRIPTION_ID: sub_id,
                        CONF_STOP_LAT:        user_input.get(CONF_STOP_LAT),
                        CONF_STOP_LON:        user_input.get(CONF_STOP_LON),
                        CONF_SCAN_INTERVAL:   user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                        "signed_in_email":    self._pax_user.get("email", "") if self._pax_user else "",
                    },
                )

        schema = vol.Schema({
            vol.Required(
                CONF_SUBSCRIPTION_ID,
                description={"suggested_value": default_sub or ""},
            ): str,
            vol.Optional(CONF_STOP_LAT): vol.Coerce(float),
            vol.Optional(CONF_STOP_LON): vol.Coerce(float),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=DEFAULT_SCAN_INTERVAL,
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
        })

        pax_info = ""
        if self._pax_user:
            pax_info = (
                f"Signed in as {self._pax_user.get('email', '')} "
                f"(user {self._pax_user.get('id', '')})"
            )

        hint = (
            f"Routes auto-discovered: {self._discovered_sub}"
            if self._discovered_sub
            else "Format:  operator_id:route_id,route_id,...  e.g. 123:1,2,3"
        )

        return self.async_show_form(
            step_id="routes",
            data_schema=schema,
            errors=errors,
            description_placeholders={"pax_info": pax_info, "hint": hint},
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TransportMeOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class TransportMeOptionsFlow(config_entries.OptionsFlow):
    """
    Configure page:
      init   – pre-filled settings + "Sign in again" toggle
      reauth – form with sign-in link
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry   = config_entry
        self._id_token:      str = config_entry.data.get(CONF_AUTH_TOKEN, "")
        self._refresh_token: str = config_entry.data.get(CONF_REFRESH_TOKEN, "")
        self._pending: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step init
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        data = self._config_entry.data
        errors: dict[str, str] = {}

        if user_input is not None:
            wants_reauth = user_input.pop("re_authenticate", False)
            self._pending = user_input
            if wants_reauth:
                return await self.async_step_reauth()
            return self._save(self._pending)

        schema = vol.Schema({
            vol.Required(
                CONF_SUBSCRIPTION_ID,
                default=data.get(CONF_SUBSCRIPTION_ID, ""),
            ): str,
            vol.Optional(
                CONF_STOP_LAT,
                default=data.get(CONF_STOP_LAT) or vol.UNDEFINED,
            ): vol.Coerce(float),
            vol.Optional(
                CONF_STOP_LON,
                default=data.get(CONF_STOP_LON) or vol.UNDEFINED,
            ): vol.Coerce(float),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
            vol.Optional("re_authenticate", default=False): bool,
        })
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "signed_in_email": data.get("signed_in_email", "your account"),
            },
        )

    # ------------------------------------------------------------------
    # Step reauth – form with sign-in link
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        First call: show form with sign-in link.
        Submit: pick up tokens stored by callback view.
        """
        # Ensure views are registered
        reg_key = f"{DOMAIN}_views_registered"
        if not self.hass.data.get(reg_key):
            from .views import TransportMeAuthView, TransportMeCallbackView
            self.hass.http.register_view(TransportMeAuthView())
            self.hass.http.register_view(TransportMeCallbackView())
            self.hass.data[reg_key] = True

        errors: dict[str, str] = {}

        if user_input is not None:
            auth_data = self.hass.data.pop(_pending_key(self.flow_id), None)
            if not auth_data:
                errors["base"] = "auth_not_completed"
            else:
                self._id_token      = auth_data["id_token"]
                self._refresh_token = auth_data["refresh_token"]
                self._pending["signed_in_email"] = auth_data.get("email", "")
                return self._save(self._pending)

        # Reserve slot and build URL
        pkey = _pending_key(self.flow_id)
        if pkey not in self.hass.data:
            self.hass.data[pkey] = None

        base     = _ha_url(self.hass)
        auth_url = f"{base}/api/transportme/auth?flow_id={self.flow_id}&flow_type=options"

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"auth_url": auth_url},
        )

    # ------------------------------------------------------------------
    # Save helper
    # ------------------------------------------------------------------

    def _save(self, settings: dict[str, Any]) -> FlowResult:
        """Merge updated settings + tokens back into the config entry."""
        data = self._config_entry.data
        updated = {
            **data,
            CONF_AUTH_TOKEN:      self._id_token,
            CONF_REFRESH_TOKEN:   self._refresh_token,
            CONF_SUBSCRIPTION_ID: settings.get(CONF_SUBSCRIPTION_ID, data.get(CONF_SUBSCRIPTION_ID)),
            CONF_STOP_LAT:        settings.get(CONF_STOP_LAT),
            CONF_STOP_LON:        settings.get(CONF_STOP_LON),
            CONF_SCAN_INTERVAL:   settings.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            "signed_in_email":    settings.get("signed_in_email", data.get("signed_in_email", "")),
        }
        self.hass.config_entries.async_update_entry(self._config_entry, data=updated)
        return self.async_create_entry(title="", data={})
