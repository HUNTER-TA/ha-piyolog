"""Unit tests for PiyoLogDataUpdateCoordinator backoff, jitter, and error handling."""

from __future__ import annotations

from datetime import timedelta
import random
from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.piyolog.api import (
    PiyoLogFeedUnavailableError,
    PiyoLogRateLimitError,
    PiyoLogResponseTooLargeError,
    PiyoLogTemporaryError,
    PiyoLogUnsupportedSchemaError,
)
from custom_components.piyolog.const import (
    MAX_BACKOFF_SECONDS,
    STATUS_OK,
    STATUS_RATE_LIMITED,
    STATUS_TEMPORARY_ERROR,
    STATUS_TOO_LARGE,
    STATUS_UNAVAILABLE,
    STATUS_UNSUPPORTED_SCHEMA,
)
from custom_components.piyolog.coordinator import PiyoLogDataUpdateCoordinator

TEST_URL = "https://feed.piyolog.com/v1/feed/24h/feed-001/secret-001"


class _StubApiClient:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    async def async_fetch_feed(self, feed_url: str) -> dict[str, Any]:
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _sample_payload(count: int = 1) -> dict[str, Any]:
    records = [
        {
            "event_id": f"ev-{i}",
            "datetime": f"2026-09-09T0{i}:00:00.000Z",
            "type": "Formula",
            "value": {"value": 100, "unit": "ml"},
        }
        for i in range(1, count + 1)
    ]
    return {
        "schema_version": 1,
        "generated_at": "2026-09-09T03:00:00.000Z",
        "range": {
            "from": "2026-09-08T03:00:00.000Z",
            "to": "2026-09-09T03:00:00.000Z",
        },
        "records": records,
    }


def test_min_interval_enforced_per_period() -> None:
    hass = HomeAssistant()
    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=_StubApiClient([]),  # type: ignore[arg-type]
        feed_url="https://feed.piyolog.com/v1/feed/28d/feed-002/secret-002",
        feed_id="feed-002",
        period="28d",
        base_interval_sec=60,  # below 28d cache TTL (900s)
        rng=random.Random(42),
    )
    assert coord.base_interval_sec == 900
    assert coord.update_interval == timedelta(seconds=900)


def test_backoff_and_jitter_progression() -> None:
    hass = HomeAssistant()
    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=_StubApiClient([]),  # type: ignore[arg-type]
        feed_url=TEST_URL,
        feed_id="feed-001",
        period="24h",
        base_interval_sec=180,
        rng=random.Random(123),
    )
    i0 = coord.compute_next_interval(0)
    i1 = coord.compute_next_interval(1)
    i2 = coord.compute_next_interval(2)
    i10 = coord.compute_next_interval(10)

    assert 180 <= i0 <= 195
    assert 360 <= i1 <= 414
    assert 720 <= i2 <= 774
    assert i10 == MAX_BACKOFF_SECONDS


async def test_error_preserves_last_snapshot_and_recovers() -> None:
    """Spec compliance: errors must never overwrite previous snapshot with 0 records."""
    hass = HomeAssistant()
    client = _StubApiClient(
        [
            _sample_payload(count=2),
            PiyoLogRateLimitError("429", error_code="rate_limit_exceeded", status_code=429),
            PiyoLogTemporaryError("500", error_code="internal_error", status_code=500),
            _sample_payload(count=3),
        ]
    )
    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=client,  # type: ignore[arg-type]
        feed_url=TEST_URL,
        feed_id="feed-001",
        period="24h",
        base_interval_sec=180,
        rng=random.Random(0),
    )

    # 1. Initial success
    d1 = await coord._async_update_data()
    assert d1.status == STATUS_OK
    assert d1.snapshot is not None
    assert len(d1.snapshot.records) == 2
    assert d1.consecutive_errors == 0
    assert d1.current_interval_sec <= 195

    # 2. 429 rate limit -> keeps 2 records, increases interval
    d2 = await coord._async_update_data()
    assert d2.status == STATUS_RATE_LIMITED
    assert d2.snapshot is not None
    assert len(d2.snapshot.records) == 2
    assert d2.last_error_code == "rate_limit_exceeded"
    assert d2.consecutive_errors == 1
    assert d2.current_interval_sec >= 360

    # 3. 500 error -> still keeps 2 records, backs off further
    d3 = await coord._async_update_data()
    assert d3.status == STATUS_TEMPORARY_ERROR
    assert d3.snapshot is not None
    assert len(d3.snapshot.records) == 2
    assert d3.consecutive_errors == 2
    assert d3.current_interval_sec >= 720

    # 4. Recovery -> updates to 3 records and resets interval
    d4 = await coord._async_update_data()
    assert d4.status == STATUS_OK
    assert d4.snapshot is not None
    assert len(d4.snapshot.records) == 3
    assert d4.consecutive_errors == 0
    assert d4.last_error_code is None
    assert d4.current_interval_sec <= 195


async def test_initial_fetch_failure_raises_update_failed() -> None:
    hass = HomeAssistant()
    client = _StubApiClient(
        [PiyoLogFeedUnavailableError("404", error_code="feed_unavailable", status_code=404)]
    )
    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=client,  # type: ignore[arg-type]
        feed_url=TEST_URL,
        feed_id="feed-001",
        period="24h",
        base_interval_sec=180,
    )

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (PiyoLogFeedUnavailableError("404", error_code="feed_unavailable"), STATUS_UNAVAILABLE),
        (PiyoLogResponseTooLargeError("413", error_code="feed_response_too_large"), STATUS_TOO_LARGE),
        (PiyoLogUnsupportedSchemaError("v2", error_code="unsupported_schema"), STATUS_UNSUPPORTED_SCHEMA),
    ],
)
async def test_permanent_error_status_mapping_after_success(
    exc: Exception, expected_status: str
) -> None:
    hass = HomeAssistant()
    client = _StubApiClient([_sample_payload(1), exc])
    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=client,  # type: ignore[arg-type]
        feed_url=TEST_URL,
        feed_id="feed-001",
        period="24h",
        base_interval_sec=180,
    )
    await coord._async_update_data()
    data = await coord._async_update_data()
    assert data.status == expected_status
    assert data.snapshot is not None
    assert len(data.snapshot.records) == 1
