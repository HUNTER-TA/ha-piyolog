"""Persistent rolling local history store for PiyoLog Data Feed records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .parser import parse_utc_datetime

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


def merge_raw_records(
    stored_records: list[dict[str, Any]],
    incoming_records: list[dict[str, Any]],
    *,
    range_from_utc: datetime,
    effective_now_utc: datetime,
    retention_days: int,
) -> list[dict[str, Any]]:
    """Merge locally stored records with the latest API snapshot.

    Rules:
    - If retention_days <= 0: return incoming_records as-is (no local accumulation).
    - Records inside the incoming snapshot window (`dt >= range_from_utc`) are always
      replaced authoritatively by `incoming_records`, ensuring edits and deletions
      within the feed window are reflected accurately.
    - Stored records older than `range_from_utc` are retained as long as:
      1. They are within `retention_days` of `effective_now_utc`.
      2. Their `event_id` is not present in `incoming_records` (handles records whose
         datetime was edited from the past into the current snapshot window).
    """
    if retention_days <= 0:
        return list(incoming_records)

    cutoff_utc = effective_now_utc - timedelta(days=retention_days)

    incoming_ids: set[str] = set()
    valid_incoming: list[tuple[datetime, dict[str, Any]]] = []
    for rec in incoming_records:
        if not isinstance(rec, dict):
            continue
        eid = rec.get("event_id")
        raw_dt = rec.get("datetime")
        if not isinstance(eid, str) or not eid or not isinstance(raw_dt, str):
            continue
        try:
            dt = parse_utc_datetime(raw_dt)
        except ValueError:
            continue
        incoming_ids.add(eid)
        valid_incoming.append((dt, rec))

    retained_past: list[tuple[datetime, dict[str, Any]]] = []
    seen_past_ids: set[str] = set()
    for rec in stored_records:
        if not isinstance(rec, dict):
            continue
        eid = rec.get("event_id")
        raw_dt = rec.get("datetime")
        if not isinstance(eid, str) or not eid or not isinstance(raw_dt, str):
            continue
        if eid in incoming_ids or eid in seen_past_ids:
            continue
        try:
            dt = parse_utc_datetime(raw_dt)
        except ValueError:
            continue
        if dt < cutoff_utc:
            continue
        if dt >= range_from_utc:
            # Inside current API window but absent from incoming -> deleted or moved
            continue
        seen_past_ids.add(eid)
        retained_past.append((dt, rec))

    combined = retained_past + valid_incoming
    combined.sort(key=lambda item: item[0])
    return [rec for _, rec in combined]


class PiyoLogHistoryStore:
    """Manages persistent rolling record history in Home Assistant .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
        )
        self._loaded = False
        self._records: list[dict[str, Any]] = []
        self._history_since: datetime | None = None
        self._last_saved_signature: str | None = None

    @property
    def history_since(self) -> datetime | None:
        """Earliest UTC timestamp covered by continuous local history."""
        return self._history_since

    @property
    def stored_records(self) -> list[dict[str, Any]]:
        """Currently loaded raw records."""
        return self._records

    async def async_load(self) -> None:
        """Load stored history from disk once."""
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return

        raw_recs = data.get("records")
        if isinstance(raw_recs, list):
            self._records = [r for r in raw_recs if isinstance(r, dict)]

        raw_since = data.get("history_since")
        if isinstance(raw_since, str):
            try:
                self._history_since = parse_utc_datetime(raw_since)
            except ValueError:
                self._history_since = None

        self._last_saved_signature = self._compute_signature(
            self._records, self._history_since
        )

    def _compute_signature(
        self,
        records: list[dict[str, Any]],
        history_since: datetime | None,
    ) -> str:
        """SHA-256 fingerprint to skip redundant disk writes when unchanged."""
        encoded = json.dumps(
            {
                "history_since": history_since.isoformat() if history_since else None,
                "records": records,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def async_merge_and_save(
        self,
        incoming_records: list[dict[str, Any]],
        *,
        range_from_utc: datetime,
        effective_now_utc: datetime,
        retention_days: int,
    ) -> tuple[list[dict[str, Any]], datetime]:
        """Merge incoming snapshot into local history and persist only on change.

        Returns `(merged_records, effective_range_from_utc)`.
        """
        await self.async_load()

        merged = merge_raw_records(
            self._records,
            incoming_records,
            range_from_utc=range_from_utc,
            effective_now_utc=effective_now_utc,
            retention_days=retention_days,
        )

        if retention_days <= 0:
            effective_since = range_from_utc
            self._records = []
            self._history_since = None
            if self._last_saved_signature is not None:
                await self._store.async_remove()
                self._last_saved_signature = None
            return merged, effective_since

        cutoff_utc = effective_now_utc - timedelta(days=retention_days)
        if self._history_since is None:
            effective_since = range_from_utc
        else:
            effective_since = max(min(self._history_since, range_from_utc), cutoff_utc)

        self._records = merged
        self._history_since = effective_since

        new_sig = self._compute_signature(merged, effective_since)
        if new_sig != self._last_saved_signature:
            await self._store.async_save(
                {
                    "history_since": effective_since.isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "records": merged,
                }
            )
            self._last_saved_signature = new_sig
            _LOGGER.debug(
                "Saved PiyoLog local history: %d records (since %s)",
                len(merged),
                effective_since.isoformat(),
            )

        return merged, effective_since

    async def async_remove(self) -> None:
        """Delete stored history file when config entry is removed."""
        self._records = []
        self._history_since = None
        self._last_saved_signature = None
        await self._store.async_remove()
