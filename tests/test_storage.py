"""Unit tests for persistent 14-day rolling local history storage and merging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.piyolog.coordinator import PiyoLogDataUpdateCoordinator
from custom_components.piyolog.storage import PiyoLogHistoryStore, merge_raw_records


def _rec(event_id: str, dt_iso: str, value_ml: int = 100) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "datetime": dt_iso,
        "type": "Formula",
        "value": {"value": value_ml, "unit": "ml"},
    }


def test_merge_raw_records_retains_past_14_days_and_reflects_window_edits() -> None:
    """Past records < range_from are kept up to retention_days; window edits/deletions apply."""
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    range_from = now - timedelta(hours=24)  # 2026-09-19T12:00Z

    stored = [
        # Older than 14 days (2026-09-05) -> must be pruned
        _rec("too-old", "2026-09-05T10:00:00.000Z", 90),
        # 5 days ago -> must be retained
        _rec("past-keep", "2026-09-15T10:00:00.000Z", 110),
        # Previously in past, but moved into current window in incoming -> deduplicated
        _rec("moved-id", "2026-09-18T10:00:00.000Z", 100),
        # Inside current window, absent from incoming -> deleted in app, must be dropped
        _rec("deleted-in-window", "2026-09-19T15:00:00.000Z", 100),
        # Inside current window, edited in incoming -> replaced with new value
        _rec("edited-in-window", "2026-09-20T01:00:00.000Z", 100),
    ]

    incoming = [
        _rec("edited-in-window", "2026-09-20T01:00:00.000Z", 140),
        _rec("moved-id", "2026-09-20T05:00:00.000Z", 120),
        _rec("brand-new", "2026-09-20T09:00:00.000Z", 130),
    ]

    merged = merge_raw_records(
        stored,
        incoming,
        range_from_utc=range_from,
        effective_now_utc=now,
        retention_days=14,
    )

    ids = [r["event_id"] for r in merged]
    assert ids == ["past-keep", "edited-in-window", "moved-id", "brand-new"]
    by_id = {r["event_id"]: r for r in merged}
    assert by_id["edited-in-window"]["value"]["value"] == 140
    assert by_id["moved-id"]["datetime"] == "2026-09-20T05:00:00.000Z"


def test_merge_raw_records_disabled_when_retention_zero() -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    stored = [_rec("past-1", "2026-09-15T10:00:00.000Z", 100)]
    incoming = [_rec("new-1", "2026-09-20T10:00:00.000Z", 120)]

    merged = merge_raw_records(
        stored,
        incoming,
        range_from_utc=now - timedelta(hours=24),
        effective_now_utc=now,
        retention_days=0,
    )
    assert [r["event_id"] for r in merged] == ["new-1"]


async def test_history_store_skips_unchanged_saves_and_detects_in_place_edits() -> None:
    hass = HomeAssistant()
    store = PiyoLogHistoryStore(hass, "entry-1")

    t1 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    rf1 = t1 - timedelta(hours=24)
    recs1 = [_rec("f1", "2026-09-20T02:00:00.000Z", 100)]

    # 1st save -> writes
    merged1, since1 = await store.async_merge_and_save(
        recs1, range_from_utc=rf1, effective_now_utc=t1, retention_days=14
    )
    assert len(merged1) == 1
    assert since1 == rf1
    assert store._store.save_count == 1  # type: ignore[attr-defined]

    # 2nd save with identical content -> skipped!
    await store.async_merge_and_save(
        recs1, range_from_utc=rf1, effective_now_utc=t1, retention_days=14
    )
    assert store._store.save_count == 1  # type: ignore[attr-defined]

    # 3rd save with same-length value edit (100 -> 120) -> SHA-256 catches it & saves!
    recs_edited = [_rec("f1", "2026-09-20T02:00:00.000Z", 120)]
    await store.async_merge_and_save(
        recs_edited, range_from_utc=rf1, effective_now_utc=t1, retention_days=14
    )
    assert store._store.save_count == 2  # type: ignore[attr-defined]

    # Setting retention_days=0 clears stored file
    await store.async_merge_and_save(
        recs_edited, range_from_utc=rf1, effective_now_utc=t1, retention_days=0
    )
    assert store._store.remove_count == 1  # type: ignore[attr-defined]


async def test_coordinator_accumulates_24h_feeds_across_days() -> None:
    """Two consecutive 24h feed snapshots build multi-day history and yesterday summary."""
    hass = HomeAssistant(time_zone="Asia/Tokyo")
    store = PiyoLogHistoryStore(hass, "entry-24h")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    day1_to = now - timedelta(days=1)
    day1_from = day1_to - timedelta(hours=24)
    day2_to = now
    day2_from = day1_to

    day1_payload = {
        "schema_version": 1,
        "generated_at": day1_to.isoformat(),
        "range": {
            "from": day1_from.isoformat(),
            "to": day1_to.isoformat(),
        },
        "records": [
            _rec("d1-f1", (day1_to - timedelta(hours=6)).isoformat(), 150),
            _rec("d1-f2", (day1_to - timedelta(hours=2)).isoformat(), 160),
        ],
    }
    day2_payload = {
        "schema_version": 1,
        "generated_at": day2_to.isoformat(),
        "range": {
            "from": day2_from.isoformat(),
            "to": day2_to.isoformat(),
        },
        "records": [
            _rec("d2-f1", (day2_to - timedelta(hours=2)).isoformat(), 180),
        ],
    }

    class _SeqClient:
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self._items = list(items)

        async def async_fetch_feed(self, feed_url: str) -> dict[str, Any]:
            return self._items.pop(0)

    coord = PiyoLogDataUpdateCoordinator(
        hass,
        config_entry=None,
        api_client=_SeqClient([day1_payload, day2_payload]),  # type: ignore[arg-type]
        feed_url="https://feed.piyolog.com/v1/feed/24h/feed-24h/secret",
        feed_id="feed-24h",
        period="24h",
        base_interval_sec=180,
        retention_days=14,
        history_store=store,
    )

    d1 = await coord._async_update_data()
    assert d1.feed_record_count == 2
    assert d1.snapshot is not None
    assert len(d1.snapshot.records) == 2

    d2 = await coord._async_update_data()
    assert d2.feed_record_count == 1  # API returned only day 2's 1 record
    assert d2.snapshot is not None
    assert len(d2.snapshot.records) == 3  # Local store preserved day 1's 2 records + day 2's 1
    assert d2.retention_days == 14
    assert len(d2.snapshot.recent_formula_records) == 3
    assert [r["value_ml"] for r in d2.snapshot.recent_formula_records] == [180.0, 160.0, 150.0]
