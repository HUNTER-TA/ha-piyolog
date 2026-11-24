"""Pytest fixtures and lightweight Home Assistant stubs for isolated unit tests."""

from __future__ import annotations

from datetime import timedelta
import logging
import sys
import types
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def _install_homeassistant_stubs() -> None:
    """Install minimal homeassistant module stubs when running outside HA Core."""
    if "homeassistant" in sys.modules:
        return

    ha_mod = types.ModuleType("homeassistant")
    core_mod = types.ModuleType("homeassistant.core")
    const_mod = types.ModuleType("homeassistant.const")
    exceptions_mod = types.ModuleType("homeassistant.exceptions")
    config_entries_mod = types.ModuleType("homeassistant.config_entries")
    helpers_mod = types.ModuleType("homeassistant.helpers")
    cv_mod = types.ModuleType("homeassistant.helpers.config_validation")
    aiohttp_client_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    storage_mod = types.ModuleType("homeassistant.helpers.storage")
    update_coord_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class Store(Generic[T]):
        def __init__(self, hass: Any, version: int, key: str) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self._data: T | None = None
            self.save_count = 0
            self.remove_count = 0

        async def async_load(self) -> T | None:
            return self._data

        async def async_save(self, data: T) -> None:
            self._data = data
            self.save_count += 1

        async def async_remove(self) -> None:
            self._data = None
            self.remove_count += 1

    class HomeAssistant:
        def __init__(self, time_zone: str = "Asia/Tokyo") -> None:
            self.config = types.SimpleNamespace(time_zone=time_zone)

    class ServiceCall:
        def __init__(self, data: dict[str, Any] | None = None) -> None:
            self.data = data or {}

    class SupportsResponse:
        ONLY = "only"

    class Platform:
        BINARY_SENSOR = "binary_sensor"
        SENSOR = "sensor"

    class ConfigEntry(Generic[T]):
        pass

    class ConfigEntryError(Exception):
        pass

    class ServiceValidationError(Exception):
        pass

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator(Generic[T]):
        def __init__(
            self,
            hass: HomeAssistant,
            logger: logging.Logger,
            *,
            name: str,
            update_interval: timedelta | None = None,
            config_entry: Any = None,
        ) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.config_entry = config_entry
            self.data: T | None = None

        async def _async_update_data(self) -> T:
            raise NotImplementedError

    core_mod.HomeAssistant = HomeAssistant
    core_mod.ServiceCall = ServiceCall
    core_mod.ServiceResponse = dict[str, Any]
    core_mod.SupportsResponse = SupportsResponse

    const_mod.Platform = Platform

    exceptions_mod.ConfigEntryError = ConfigEntryError
    exceptions_mod.ServiceValidationError = ServiceValidationError

    config_entries_mod.ConfigEntry = ConfigEntry

    cv_mod.string = str
    cv_mod.ensure_list = lambda x: x if isinstance(x, list) else [x]

    aiohttp_client_mod.async_get_clientsession = lambda hass: None
    storage_mod.Store = Store

    update_coord_mod.DataUpdateCoordinator = DataUpdateCoordinator
    update_coord_mod.UpdateFailed = UpdateFailed

    # Optional voluptuous stub if not installed in dev venv
    if "voluptuous" not in sys.modules:
        vol_mod = types.ModuleType("voluptuous")
        vol_mod.Schema = lambda x: x
        vol_mod.Optional = lambda key, default=None: key
        vol_mod.All = lambda *args: args
        vol_mod.Coerce = lambda t: t
        vol_mod.Range = lambda **kwargs: kwargs
        sys.modules["voluptuous"] = vol_mod

    sys.modules["homeassistant"] = ha_mod
    sys.modules["homeassistant.core"] = core_mod
    sys.modules["homeassistant.const"] = const_mod
    sys.modules["homeassistant.exceptions"] = exceptions_mod
    sys.modules["homeassistant.config_entries"] = config_entries_mod
    sys.modules["homeassistant.helpers"] = helpers_mod
    sys.modules["homeassistant.helpers.config_validation"] = cv_mod
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client_mod
    sys.modules["homeassistant.helpers.storage"] = storage_mod
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coord_mod


_install_homeassistant_stubs()
