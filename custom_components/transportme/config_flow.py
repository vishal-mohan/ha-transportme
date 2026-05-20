"""Config flow for TransportMe Bus Tracker – seamless Firebase auth built in."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
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

_LOGGER = logging.getLogger(__name__)

# Public client-side key embedded in the TransportMe app bundle (safe to publish)
FIREBASE_API_KEY   = "AIzaSyD9xVRwjC0V-FHj5D97pwD8oGUNCufs9vI"
FIREBASE_SIGN_IN   = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
GRAPHQL_URL        = "https://production.api2.transportme.com.au/"

# ---------------------------------------------------------------------------
# Firebase helpers
# ---------------------------------------------------------------------------

async def _firebase_sign_in(email: str, password: str) -> dict[str, str]:
    """
    Sign in to Firebase with email + password.
    Returns {"id_token": ..., "refresh_token": ...} or raises ValueError.
    """
    async with aiohttp.ClientSession() as session:
        async with session.post(
            FIREBASE_SIGN_IN,
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                error = body.get("error", {})
                code  = error.get("message", "UNKNOWN")
                friendly = {
                    "EMAIL_NOT_FOUND":         "No account found with that email address.",
                    "INVALID_PASSWORD":        "Incorrect password. Please try again.",
                    "INVALID_EMAIL":           "Invalid email address.",
                    "USER_DISABLED":           "This account has been disabled.",
                    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many failed attempts. Please wait and try again.",
                }.get(code, f"Sign-in failed: {code}")
                raise ValueError(friendly)
            return {
                "id_token":      body["idToken"],
                "refresh_token": body["refreshToken"],
            }


async def _verify_api(id_token: str) -> dict | None:
    """
    Quick sanity-check: call paxUser to confirm the token works against
    the TransportMe API.  Returns paxUser data or None on soft failures.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GRAPHQL_URL,
                json={"query": "query { paxUser { id email fav_operator_id } }"},
                headers={
                    "Authorization": f"Bearer {id_token}",
                    "Content-Type":  "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (401, 403):
                    return None
                body = await resp.json(content_type=None)
                return body.get("data", {}).get("paxUser")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class TransportMeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Multi-step config flow:
      Step 1 (credentials) – email + password  →  Firebase auth
      Step 2 (routes)      – subscription ID, stop coords, poll interval
    """

    VERSION = 1

    def __init__(self) -> None:
        self._id_token:      str = ""
        self._refresh_token: str = ""
        self._pax_user:      dict | None = None

    # ------------------------------------------------------------------
    # Step 1 – Credentials
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email    = user_input["email"].strip()
            password = user_input["password"]
            try:
                tokens = await _firebase_sign_in(email, password)
                self._id_token      = tokens["id_token"]
                self._refresh_token = tokens["refresh_token"]
            except ValueError as exc:
                errors["base"] = str(exc)
            except aiohttp.ClientError:
                errors["base"] = "Cannot connect to authentication server. Check your internet connection."
            except Exception:
                errors["base"] = "Unexpected error during sign-in. Please try again."

            if not errors:
                # Verify against the TransportMe API
                self._pax_user = await _verify_api(self._id_token)
                if self._pax_user is None:
                    errors["base"] = (
                        "Signed in to Firebase but TransportMe API rejected the token. "
                        "Make sure you are using your TransportMe account credentials."
                    )

            if not errors:
                return await self.async_step_routes()

        schema = vol.Schema({
            vol.Required("email",    description={"suggested_value": ""}): str,
            vol.Required("password", description={"suggested_value": ""}): str,
        })

        op_name = ""
        if self._pax_user:
            op_name = str(self._pax_user.get("fav_operator_id", ""))

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "title": "Sign in with your TransportMe email and password",
            },
        )

    # ------------------------------------------------------------------
    # Step 2 – Routes & settings
    # ------------------------------------------------------------------

    async def async_step_routes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        # Pre-fill operator id from paxUser if available
        default_sub = ""
        if self._pax_user and self._pax_user.get("fav_operator_id"):
            op = self._pax_user["fav_operator_id"]
            default_sub = f"{op}:"   # user completes with route IDs

        if user_input is not None:
            sub_id = user_input[CONF_SUBSCRIPTION_ID].strip()
            if not sub_id or ":" not in sub_id:
                errors[CONF_SUBSCRIPTION_ID] = (
                    "Enter as  operator_id:route_id,route_id  "
                    "e.g.  123:1,2,3"
                )

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
                        # Store email so options flow can display it
                        "signed_in_email":    self._pax_user.get("email", "") if self._pax_user else "",
                    },
                )

        schema = vol.Schema({
            vol.Required(
                CONF_SUBSCRIPTION_ID,
                description={"suggested_value": default_sub or "123:1,2,3"},
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
            pax_info = f"Signed in as {self._pax_user.get('email', '')} (user {self._pax_user.get('id', '')})"

        return self.async_show_form(
            step_id="routes",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "pax_info": pax_info,
                "hint": (
                    "Subscription ID format:  operator_id:route_id,route_id,...\n"
                    "Example: 123:1,2,3"
                ),
            },
        )

    # ------------------------------------------------------------------
    # Options flow (Configure button after setup)
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TransportMeOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow – settings + optional re-authentication
# ---------------------------------------------------------------------------

class TransportMeOptionsFlow(config_entries.OptionsFlow):
    """
    Configure page flow:
      Step init    – settings form (pre-filled), with a re-authenticate toggle
      Step reauth  – email + password (only if re-authenticate was ticked)
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry   = config_entry
        self._id_token:      str = config_entry.data.get(CONF_AUTH_TOKEN, "")
        self._refresh_token: str = config_entry.data.get(CONF_REFRESH_TOKEN, "")
        # Pending settings saved while we do re-auth
        self._pending: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step init – main settings, shown immediately when Configure is clicked
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        data   = self._config_entry.data
        errors: dict[str, str] = {}
        signed_in_email = data.get("signed_in_email", "your account")

        if user_input is not None:
            wants_reauth = user_input.pop("re_authenticate", False)
            self._pending = user_input

            if wants_reauth:
                # Go to re-auth step before saving
                return await self.async_step_reauth()

            # Save immediately with existing tokens
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
                "signed_in_email": signed_in_email,
            },
        )

    # ------------------------------------------------------------------
    # Step reauth – only shown when re_authenticate toggle is ticked
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email    = (user_input.get("email") or "").strip()
            password = (user_input.get("password") or "").strip()
            try:
                tokens = await _firebase_sign_in(email, password)
                self._id_token      = tokens["id_token"]
                self._refresh_token = tokens["refresh_token"]
                # Store new email for future display
                self._pending["signed_in_email"] = email
            except ValueError as exc:
                errors["base"] = str(exc)
            except Exception:
                errors["base"] = "Sign-in failed. Please check your credentials and try again."

            if not errors:
                return self._save(self._pending)

        schema = vol.Schema({
            vol.Required("email"):    str,
            vol.Required("password"): str,
        })

        return self.async_show_form(
            step_id="reauth",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "current_email": self._config_entry.data.get("signed_in_email", ""),
            },
        )

    # ------------------------------------------------------------------
    # Save helper
    # ------------------------------------------------------------------

    def _save(self, settings: dict[str, Any]) -> FlowResult:
        """Merge updated settings + current tokens back into config entry."""
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
