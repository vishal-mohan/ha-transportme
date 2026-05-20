"""Sensor platform for TransportMe – ETA, distance, speed, status."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfSpeed, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COORDINATOR, DOMAIN
from .coordinator import TransportMeCoordinator


@dataclass
class TransportMeSensorDescription(SensorEntityDescription):
    data_key: str = ""


SENSOR_DESCRIPTIONS: tuple[TransportMeSensorDescription, ...] = (
    TransportMeSensorDescription(
        key="eta_minutes",
        data_key="eta_minutes",
        name="Bus ETA",
        icon="mdi:clock-fast",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TransportMeSensorDescription(
        key="distance_km",
        data_key="distance_km",
        name="Bus Distance",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    TransportMeSensorDescription(
        key="speed",
        data_key="speed",
        name="Bus Speed",
        icon="mdi:speedometer",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    TransportMeSensorDescription(
        key="status",
        data_key="status",
        name="Bus Status",
        icon="mdi:information-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TransportMeCoordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    async_add_entities(
        [
            TransportMeSensor(coordinator, entry, description)
            for description in SENSOR_DESCRIPTIONS
        ]
    )


class TransportMeSensor(CoordinatorEntity[TransportMeCoordinator], SensorEntity):
    """A single TransportMe sensor (ETA, distance, speed, or status)."""

    entity_description: TransportMeSensorDescription

    def __init__(
        self,
        coordinator: TransportMeCoordinator,
        entry: ConfigEntry,
        description: TransportMeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        sub_id = entry.data.get("subscription_id", "")
        self._attr_name = f"TransportMe {sub_id} {description.name}"

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None
