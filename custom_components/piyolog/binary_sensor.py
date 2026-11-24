"""Binary sensor platform for PiyoLog Data Feed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PiyoLogConfigEntry
from .const import DOMAIN, TYPE_SLEEP, TYPE_WAKE_UP
from .coordinator import PiyoLogDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PiyoLogConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiyoLog binary sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([PiyoLogSleepingBinarySensor(coordinator, entry)])


class PiyoLogSleepingBinarySensor(
    CoordinatorEntity[PiyoLogDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether the child is currently sleeping."""

    _attr_has_entity_name = True
    _attr_translation_key = "sleeping"
    _attr_icon = "mdi:sleep"

    def __init__(
        self,
        coordinator: PiyoLogDataUpdateCoordinator,
        entry: PiyoLogConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_sleeping"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="PiyoLog",
            model=f"Data Feed ({coordinator.period})",
            configuration_url="https://www.piyolog.com/app/piyolog/data_feed/ja/",
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the latest sleep event is Sleep, False if WakeUp."""
        data = self.coordinator.data
        if data is None or data.snapshot is None:
            return None
        return data.snapshot.sleep.is_sleeping

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional sleep state metadata."""
        data = self.coordinator.data
        if data is None or data.snapshot is None:
            return {}

        snapshot = data.snapshot
        sleep = snapshot.sleep
        attrs: dict[str, Any] = {
            "last_event_type": sleep.last_sleep_event_type,
            "last_sleep_duration_min": sleep.last_sleep_duration_min,
            "is_partial_boundary": sleep.is_partial_boundary,
        }
        if sleep.current_sleep_since is not None:
            attrs["since"] = sleep.current_sleep_since.isoformat()

        latest_event = None
        if sleep.last_sleep_event_type == TYPE_SLEEP:
            latest_event = snapshot.latest_by_type.get(TYPE_SLEEP)
        elif sleep.last_sleep_event_type == TYPE_WAKE_UP:
            latest_event = snapshot.latest_by_type.get(TYPE_WAKE_UP)
        if latest_event is not None and latest_event.memo is not None:
            attrs["memo"] = latest_event.memo

        return attrs
