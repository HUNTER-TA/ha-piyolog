"""DataUpdateCoordinator for the PiyoLog Data Feed integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import logging
import random
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PiyoLogApiClient,
    PiyoLogError,
    PiyoLogFeedUnavailableError,
    PiyoLogRateLimitError,
    PiyoLogResponseTooLargeError,
    PiyoLogTemporaryError,
    PiyoLogUnsupportedSchemaError,
    redact_feed_url,
)
from .const import (
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    MAX_BACKOFF_SECONDS,
    STATUS_OK,
    STATUS_RATE_LIMITED,
    STATUS_TEMPORARY_ERROR,
    STATUS_TOO_LARGE,
    STATUS_UNAVAILABLE,
    STATUS_UNSUPPORTED_SCHEMA,
    min_interval_for_period,
)
from .parser import PiyoLogSnapshot, parse_feed_payload, parse_utc_datetime
from .storage import PiyoLogHistoryStore

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PiyoLogCoordinatorData:
    """Data exposed by PiyoLogDataUpdateCoordinator to entities and services."""

    snapshot: PiyoLogSnapshot | None
    status: str
    period: str
    last_success: datetime | None
    last_error_code: str | None
    consecutive_errors: int
    current_interval_sec: int
    retention_days: int = DEFAULT_RETENTION_DAYS
    feed_record_count: int = 0
    history_since: datetime | None = None


def _resolve_tz(hass: HomeAssistant) -> tzinfo:
    """Resolve Home Assistant's configured local timezone safely."""
    tz_name = getattr(getattr(hass, "config", None), "time_zone", None)
    if isinstance(tz_name, str) and tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            _LOGGER.warning("Unknown timezone %r, falling back to UTC", tz_name)
    return timezone.utc


class PiyoLogDataUpdateCoordinator(DataUpdateCoordinator[PiyoLogCoordinatorData]):
    """Coordinator managing polling, backoff+jitter, and snapshot preservation."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry | None,
        api_client: PiyoLogApiClient,
        feed_url: str,
        feed_id: str,
        period: str,
        base_interval_sec: int,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        history_store: PiyoLogHistoryStore | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.api_client = api_client
        self._feed_url = feed_url
        self.feed_id = feed_id
        self.period = period
        self.redacted_url = redact_feed_url(feed_url)
        self.retention_days = max(0, int(retention_days))
        self._history_store = history_store
        self._rng = rng or random.Random()

        min_sec = min_interval_for_period(period)
        self.base_interval_sec = max(int(base_interval_sec), min_sec)
        self._consecutive_errors = 0
        self._last_snapshot: PiyoLogSnapshot | None = None
        self._last_success: datetime | None = None
        self._last_feed_record_count: int = 0
        self._last_history_since: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{feed_id}",
            update_interval=timedelta(seconds=self.base_interval_sec),
        )

    def compute_next_interval(self, consecutive_errors: int) -> int:
        """Compute next polling interval in seconds with backoff and jitter.

        - Normal (0 errors): base interval + small random jitter (up to 15s / 10%)
          to avoid synchronized spikes across multiple feeds.
        - Errors (n >= 1): exponential backoff capped at MAX_BACKOFF_SECONDS + jitter.
        """
        if consecutive_errors <= 0:
            jitter_max = min(15.0, self.base_interval_sec * 0.1)
            jitter = self._rng.uniform(0.0, jitter_max)
            return max(self.base_interval_sec, int(round(self.base_interval_sec + jitter)))

        exp_delay = min(
            self.base_interval_sec * (2 ** min(consecutive_errors, 10)),
            MAX_BACKOFF_SECONDS,
        )
        jitter = self._rng.uniform(0.0, self.base_interval_sec * 0.3)
        return min(MAX_BACKOFF_SECONDS, int(round(exp_delay + jitter)))

    def _status_for_error(self, err: PiyoLogError) -> str:
        """Map API exception type to diagnostic status string."""
        if isinstance(err, PiyoLogRateLimitError):
            return STATUS_RATE_LIMITED
        if isinstance(err, PiyoLogFeedUnavailableError):
            return STATUS_UNAVAILABLE
        if isinstance(err, PiyoLogResponseTooLargeError):
            return STATUS_TOO_LARGE
        if isinstance(err, PiyoLogUnsupportedSchemaError):
            return STATUS_UNSUPPORTED_SCHEMA
        return STATUS_TEMPORARY_ERROR

    async def _async_update_data(self) -> PiyoLogCoordinatorData:
        """Fetch latest PiyoLog snapshot while honoring spec error rules."""
        now_utc = datetime.now(timezone.utc)
        tz = _resolve_tz(self.hass)

        try:
            payload: dict[str, Any] = await self.api_client.async_fetch_feed(
                self._feed_url
            )
            incoming_records = payload.get("records", [])
            feed_record_count = len(incoming_records)
            api_range_from = parse_utc_datetime(payload["range"]["from"])
            generated_at = parse_utc_datetime(payload["generated_at"])
            effective_now = max(now_utc, generated_at)

            effective_since: datetime | None = None
            parse_target = payload
            if self._history_store is not None:
                merged_records, effective_since = (
                    await self._history_store.async_merge_and_save(
                        incoming_records,
                        range_from_utc=api_range_from,
                        effective_now_utc=effective_now,
                        retention_days=self.retention_days,
                    )
                )
                parse_target = {**payload, "records": merged_records}

            snapshot = parse_feed_payload(
                parse_target,
                tz=tz,
                now_utc=now_utc,
                effective_range_from_utc=effective_since,
            )
        except PiyoLogError as err:
            self._consecutive_errors += 1
            next_sec = self.compute_next_interval(self._consecutive_errors)
            self.update_interval = timedelta(seconds=next_sec)
            status = self._status_for_error(err)

            if isinstance(
                err,
                (
                    PiyoLogFeedUnavailableError,
                    PiyoLogResponseTooLargeError,
                    PiyoLogUnsupportedSchemaError,
                ),
            ):
                _LOGGER.error(
                    "PiyoLog feed error [%s] for %s (consecutive=%d, next_retry=%ds)",
                    err.error_code,
                    self.redacted_url,
                    self._consecutive_errors,
                    next_sec,
                )
            else:
                _LOGGER.warning(
                    "PiyoLog temporary error [%s] for %s (consecutive=%d, next_retry=%ds)",
                    err.error_code,
                    self.redacted_url,
                    self._consecutive_errors,
                    next_sec,
                )

            # Per PiyoLog spec: never treat an error as 0 records.
            # Preserve the last valid snapshot if we have one, while updating
            # diagnostic status so users/automations can detect the failure.
            if self._last_snapshot is not None:
                return PiyoLogCoordinatorData(
                    snapshot=self._last_snapshot,
                    status=status,
                    period=self.period,
                    last_success=self._last_success,
                    last_error_code=err.error_code,
                    consecutive_errors=self._consecutive_errors,
                    current_interval_sec=next_sec,
                    retention_days=self.retention_days,
                    feed_record_count=self._last_feed_record_count,
                    history_since=self._last_history_since,
                )

            raise UpdateFailed(
                f"Failed to fetch PiyoLog feed ({err.error_code or 'error'})"
            ) from err

        self._consecutive_errors = 0
        self._last_snapshot = snapshot
        self._last_success = now_utc
        self._last_feed_record_count = feed_record_count
        self._last_history_since = effective_since or snapshot.range_from
        next_sec = self.compute_next_interval(0)
        self.update_interval = timedelta(seconds=next_sec)

        if snapshot.unknown_types:
            _LOGGER.debug(
                "Ignored unknown PiyoLog record types for %s: %s",
                self.redacted_url,
                ", ".join(snapshot.unknown_types),
            )

        return PiyoLogCoordinatorData(
            snapshot=snapshot,
            status=STATUS_OK,
            period=self.period,
            last_success=now_utc,
            last_error_code=None,
            consecutive_errors=0,
            current_interval_sec=next_sec,
            retention_days=self.retention_days,
            feed_record_count=feed_record_count,
            history_since=self._last_history_since,
        )
