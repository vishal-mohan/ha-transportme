"""Device tracker platform for TransportMe – shows bus on the HA map."""
from __future__ import annotations

from homeassistant.components.device_tracker import SOURCE_TYPE_GPS
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COORDINATOR, DOMAIN
from .coordinator import TransportMeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TransportMeCoordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    async_add_entities([TransportMeBusTracker(coordinator, entry)])


class TransportMeBusTracker(CoordinatorEntity[TransportMeCoordinator], TrackerEntity):
    """Represents the live position of the tracked bus on the HA map."""

    _attr_icon = "mdi:bus-clock"
    _attr_source_type = SOURCE_TYPE_GPS

    def __init__(self, coordinator: TransportMeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tracker"
        self._attr_name = f"TransportMe Bus {entry.data.get('subscription_id', '')}"

    @property
    def latitude(self) -> float | None:
        return self.coordinator.data.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self.coordinator.data.get("longitude")

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data or {}
        attrs = {}
        for key in ("speed", "heading", "status", "vehicle_id", "route", "eta_minutes", "distance_km"):
            if d.get(key) is not None:
                attrs[key] = d[key]
        return attrs

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.data.get("latitude") is not None
        )
