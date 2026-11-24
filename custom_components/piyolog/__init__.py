"""The PiyoLog Data Feed integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    CONF_RETENTION_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    MAX_RETENTION_DAYS,
    SERVICE_DEFAULT_LIMIT,
    SERVICE_GET_DAILY_SUMMARY,
    SERVICE_GET_RECORDS,
    SERVICE_MAX_LIMIT,
    default_interval_for_period,
)
from .coordinator import PiyoLogDataUpdateCoordinator
from .storage import PiyoLogHistoryStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type PiyoLogConfigEntry = ConfigEntry[PiyoLogDataUpdateCoordinator]

SERVICE_GET_RECORDS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("types"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("days"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_RETENTION_DAYS)
        ),
        vol.Optional("limit", default=SERVICE_DEFAULT_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=SERVICE_MAX_LIMIT)
        ),
    }
)

SERVICE_GET_DAILY_SUMMARY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("days", default=DEFAULT_RETENTION_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_RETENTION_DAYS)
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
    retention_days = int(
        entry.options.get(
            CONF_RETENTION_DAYS,
            entry.data.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
        )
    )

    session = async_get_clientsession(hass)
    api_client = PiyoLogApiClient(session)
    history_store = PiyoLogHistoryStore(hass, entry.entry_id)
    coordinator = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=entry,
        api_client=api_client,
        feed_url=parsed.url,
        feed_id=parsed.feed_id,
        period=parsed.period,
        base_interval_sec=int(scan_interval),
        retention_days=retention_days,
        history_store=history_store,
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
        if not remaining:
            for service_name in (SERVICE_GET_RECORDS, SERVICE_GET_DAILY_SUMMARY):
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: PiyoLogConfigEntry) -> None:
    """Clean up persisted local history when a config entry is deleted."""
    await PiyoLogHistoryStore(hass, entry.entry_id).async_remove()


def _resolve_target_entry(
    hass: HomeAssistant, entry_id: str | None
) -> PiyoLogConfigEntry:
    """Resolve the target loaded PiyoLog ConfigEntry for service calls."""
    loaded_entries: list[PiyoLogConfigEntry] = (
        hass.config_entries.async_loaded_entries(DOMAIN)
    )
    if not loaded_entries:
        raise ServiceValidationError("No loaded PiyoLog entries available")

    if entry_id:
        for e in loaded_entries:
            if e.entry_id == entry_id:
                return e
        raise ServiceValidationError(f"PiyoLog entry '{entry_id}' is not loaded")

    if len(loaded_entries) == 1:
        return loaded_entries[0]

    raise ServiceValidationError(
        "Multiple PiyoLog feeds are configured; please specify entry_id"
    )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if not hass.services.has_service(DOMAIN, SERVICE_GET_RECORDS):

        async def _handle_get_records(call: ServiceCall) -> ServiceResponse:
            target_entry = _resolve_target_entry(hass, call.data.get("entry_id"))
            types_filter: list[str] | None = call.data.get("types")
            days_filter: int | None = call.data.get("days")
            limit: int = call.data.get("limit", SERVICE_DEFAULT_LIMIT)

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
            cutoff_utc: datetime | None = (
                max(datetime.now(timezone.utc), snapshot.generated_at)
                - timedelta(days=days_filter)
                if days_filter is not None
                else None
            )
            matched: list[dict[str, Any]] = []
            for rec in reversed(snapshot.records):
                if cutoff_utc is not None and rec.dt < cutoff_utc:
                    break
                if allowed_types is not None and rec.record_type not in allowed_types:
                    continue
                matched.append(rec.to_dict())
                if len(matched) >= limit:
                    break

            return {
                "entry_id": target_entry.entry_id,
                "name": target_entry.title,
                "period": data.period,
                "retention_days": data.retention_days,
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

    if not hass.services.has_service(DOMAIN, SERVICE_GET_DAILY_SUMMARY):

        async def _handle_get_daily_summary(call: ServiceCall) -> ServiceResponse:
            target_entry = _resolve_target_entry(hass, call.data.get("entry_id"))
            days: int = call.data.get("days", 14)

            coordinator = target_entry.runtime_data
            data = coordinator.data
            snapshot = data.snapshot if data else None
            if snapshot is None:
                return {
                    "entry_id": target_entry.entry_id,
                    "name": target_entry.title,
                    "status": data.status if data else "unknown",
                    "daily": [],
                    "sleep_sessions": [],
                }

            daily_slice = snapshot.daily_summaries[-days:]
            return {
                "entry_id": target_entry.entry_id,
                "name": target_entry.title,
                "period": data.period,
                "retention_days": data.retention_days,
                "history_since": (
                    data.history_since.isoformat() if data.history_since else None
                ),
                "status": data.status,
                "generated_at": snapshot.generated_at.isoformat(),
                "range": {
                    "from": snapshot.range_from.isoformat(),
                    "to": snapshot.range_to.isoformat(),
                },
                "daily": [d.to_dict() for d in daily_slice],
                "averages_7d": {
                    "formula_ml": snapshot.avg_7d_formula_ml,
                    "diaper_count": snapshot.avg_7d_diaper_count,
                    "sleep_min": snapshot.avg_7d_sleep_min,
                },
                "intervals": {
                    "feeding": snapshot.feeding_interval.to_dict(),
                    "diaper": snapshot.diaper_interval.to_dict(),
                    "pee": snapshot.pee_interval.to_dict(),
                    "poop": snapshot.poop_interval.to_dict(),
                },
                "recent_formula": list(snapshot.recent_formula_records),
                "sleep_sessions": [s.to_dict() for s in snapshot.sleep.sessions],
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_DAILY_SUMMARY,
            _handle_get_daily_summary,
            schema=SERVICE_GET_DAILY_SUMMARY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
