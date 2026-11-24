"""The PiyoLog Data Feed integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PiyoLogApiClient, PiyoLogInvalidUrlError, parse_feed_url
from .const import (
    CONF_FEED_URL,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_DEFAULT_LIMIT,
    SERVICE_GET_RECORDS,
    SERVICE_MAX_LIMIT,
    default_interval_for_period,
)
from .coordinator import PiyoLogDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PiyoLogConfigEntry = ConfigEntry[PiyoLogDataUpdateCoordinator]

SERVICE_GET_RECORDS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("types"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("limit", default=SERVICE_DEFAULT_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=SERVICE_MAX_LIMIT)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: PiyoLogConfigEntry) -> bool:
    """Set up PiyoLog Data Feed from a config entry."""
    feed_url: str = entry.data[CONF_FEED_URL]
    try:
        parsed = parse_feed_url(feed_url)
    except PiyoLogInvalidUrlError as err:
        raise ConfigEntryError("Configured PiyoLog feed URL is invalid") from err

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, default_interval_for_period(parsed.period)),
    )

    session = async_get_clientsession(hass)
    api_client = PiyoLogApiClient(session)
    coordinator = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=entry,
        api_client=api_client,
        feed_url=parsed.url,
        feed_id=parsed.feed_id,
        period=parsed.period,
        base_interval_sec=int(scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: PiyoLogConfigEntry
) -> None:
    """Handle options or entry updates by reloading the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PiyoLogConfigEntry) -> bool:
    """Unload a PiyoLog config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        remaining = [
            e
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining and hass.services.has_service(DOMAIN, SERVICE_GET_RECORDS):
            hass.services.async_remove(DOMAIN, SERVICE_GET_RECORDS)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_RECORDS):
        return

    async def _handle_get_records(call: ServiceCall) -> ServiceResponse:
        entry_id: str | None = call.data.get("entry_id")
        types_filter: list[str] | None = call.data.get("types")
        limit: int = call.data.get("limit", SERVICE_DEFAULT_LIMIT)

        loaded_entries: list[PiyoLogConfigEntry] = (
            hass.config_entries.async_loaded_entries(DOMAIN)
        )
        if not loaded_entries:
            raise ServiceValidationError("No loaded PiyoLog entries available")

        target_entry: PiyoLogConfigEntry | None = None
        if entry_id:
            for e in loaded_entries:
                if e.entry_id == entry_id:
                    target_entry = e
                    break
            if target_entry is None:
                raise ServiceValidationError(
                    f"PiyoLog entry '{entry_id}' is not loaded"
                )
        elif len(loaded_entries) == 1:
            target_entry = loaded_entries[0]
        else:
            raise ServiceValidationError(
                "Multiple PiyoLog feeds are configured; please specify entry_id"
            )

        coordinator = target_entry.runtime_data
        data = coordinator.data
        snapshot = data.snapshot if data else None
        if snapshot is None:
            return {
                "entry_id": target_entry.entry_id,
                "name": target_entry.title,
                "status": data.status if data else "unknown",
                "count": 0,
                "records": [],
            }

        allowed_types = set(types_filter) if types_filter else None
        matched: list[dict[str, Any]] = []
        for rec in reversed(snapshot.records):
            if allowed_types is not None and rec.record_type not in allowed_types:
                continue
            matched.append(rec.to_dict())
            if len(matched) >= limit:
                break

        return {
            "entry_id": target_entry.entry_id,
            "name": target_entry.title,
            "period": data.period,
            "status": data.status,
            "generated_at": snapshot.generated_at.isoformat(),
            "range": {
                "from": snapshot.range_from.isoformat(),
                "to": snapshot.range_to.isoformat(),
            },
            "count": len(matched),
            "total_in_snapshot": len(snapshot.records),
            "records": matched,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECORDS,
        _handle_get_records,
        schema=SERVICE_GET_RECORDS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
