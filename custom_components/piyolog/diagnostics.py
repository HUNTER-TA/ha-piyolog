"""Diagnostics support for PiyoLog Data Feed (with secret redaction)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PiyoLogConfigEntry
from .const import CONF_FEED_URL

TO_REDACT = {CONF_FEED_URL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: PiyoLogConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a PiyoLog config entry without exposing secrets or memos."""
    coordinator = entry.runtime_data
    data = coordinator.data
    snapshot = data.snapshot if data else None

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "feed_url_redacted": coordinator.redacted_url,
            "period": coordinator.period,
            "retention_days": coordinator.retention_days,
            "history_since": (
                data.history_since.isoformat() if data and data.history_since else None
            ),
            "feed_record_count": data.feed_record_count if data else None,
            "base_interval_sec": coordinator.base_interval_sec,
            "status": data.status if data else None,
            "last_success": (
                data.last_success.isoformat() if data and data.last_success else None
            ),
            "last_error_code": data.last_error_code if data else None,
            "consecutive_errors": data.consecutive_errors if data else None,
            "current_interval_sec": data.current_interval_sec if data else None,
        },
        "snapshot_summary": (
            {
                "schema_version": snapshot.schema_version,
                "generated_at": snapshot.generated_at.isoformat(),
                "range_from": snapshot.range_from.isoformat(),
                "range_to": snapshot.range_to.isoformat(),
                "range_covers_today": snapshot.range_covers_today,
                "total_records": len(snapshot.records),
                "counts_by_type": snapshot.counts_by_type,
                "unknown_types": list(snapshot.unknown_types),
            }
            if snapshot
            else None
        ),
    }
