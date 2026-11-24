"""Unit tests for PiyoLog Data Feed JSON parser and aggregator."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from custom_components.piyolog.const import KNOWN_RECORD_TYPES, RECENT_RECORDS_LIMIT
from custom_components.piyolog.parser import parse_feed_payload

JST = ZoneInfo("Asia/Tokyo")


def test_parse_official_example_payload() -> None:
    """Verify exact parsing of the example payload from PiyoLog official docs."""
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-09T03:00:00.000Z",
        "range": {
            "from": "2026-09-08T03:00:00.000Z",
            "to": "2026-09-09T03:00:00.000Z",
        },
        "records": [
            {
                "event_id": "example-001",
                "datetime": "2026-09-08T07:00:00.000Z",
                "type": "Formula",
                "value": {"value": 120, "unit": "ml"},
                "memo": "Finished the bottle.",
            },
            {
                "event_id": "example-002",
                "datetime": "2026-09-08T10:00:00.000Z",
                "type": "BreastFeeding",
                "last": "right",
                "leftTime": 300,
                "rightTime": 240,
                "value": {"value": 80, "unit": "ml"},
            },
            {
                "event_id": "example-003",
                "datetime": "2026-09-09T01:00:00.000Z",
                "type": "Poop",
                "details": {
                    "amount": "normal",
                    "hardness": "soft",
                    "color": "yellow",
                },
            },
            {
                "event_id": "example-004",
                "datetime": "2026-09-09T02:00:00.000Z",
                "type": "Memo",
                "memo": "Packed a spare outfit.",
            },
        ],
    }

    snap = parse_feed_payload(
        payload,
        tz=JST,
        now_utc=datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc),
    )

    assert snap.schema_version == 1
    assert len(snap.records) == 4
    assert snap.latest_record is not None
    assert snap.latest_record.event_id == "example-004"
    assert snap.latest_record.record_type == "Memo"
    assert snap.latest_record.memo == "Packed a spare outfit."

    assert snap.latest_feeding is not None
    assert snap.latest_feeding.event_id == "example-002"
    assert snap.latest_feeding.last_side == "right"
    assert snap.latest_feeding.left_time == 300
    assert snap.latest_feeding.right_time == 240
    assert snap.latest_feeding.total_breastfeeding_time_sec == 540
    assert snap.latest_feeding.value == 80.0

    poop = snap.latest_by_type["Poop"]
    assert poop.poop_amount == "normal"
    assert poop.poop_hardness == "soft"
    assert poop.poop_color == "yellow"

    # In JST (UTC+9): 2026-09-09T03:00Z is 2026-09-09 12:00 JST.
    # Today start is 2026-09-08T15:00Z, so range.from (2026-09-08T03:00Z) covers today.
    assert snap.range_covers_today is True
    assert snap.today_counts_by_type.get("Poop") == 1
    assert snap.today_counts_by_type.get("Memo") == 1
    assert snap.today_counts_by_type.get("Formula", 0) == 0
    assert snap.last_24h_formula_ml == 120.0
    assert snap.last_24h_breastfeeding_ml == 80.0
    assert snap.unknown_types == ()


def test_omitted_numeric_values_are_not_treated_as_zero() -> None:
    """Per spec: omitted value/details must remain None, never 0."""
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-09T06:00:00.000Z",
        "range": {
            "from": "2026-09-08T06:00:00.000Z",
            "to": "2026-09-09T06:00:00.000Z",
        },
        "records": [
            {
                "event_id": "f-1",
                "datetime": "2026-09-09T01:00:00.000Z",
                "type": "Formula",
                # value omitted
            },
            {
                "event_id": "bf-1",
                "datetime": "2026-09-09T02:00:00.000Z",
                "type": "BreastFeeding",
                # last / leftTime / rightTime / value all omitted
            },
            {
                "event_id": "poop-1",
                "datetime": "2026-09-09T03:00:00.000Z",
                "type": "Poop",
                "details": {"color": "brown"},
            },
            {
                "event_id": "temp-1",
                "datetime": "2026-09-09T04:00:00.000Z",
                "type": "Temperature",
                "value": {"value": 36.8, "unit": "celsius"},
            },
            {
                "event_id": "temp-2",
                "datetime": "2026-09-09T05:00:00.000Z",
                "type": "Temperature",
                # value omitted on newer record; latest measurement should keep 36.8
            },
            {
                "event_id": "foot-1",
                "datetime": "2026-09-09T05:10:00.000Z",
                "type": "Foot",
                "value": {"left": 12.3, "unit": "cm"},
            },
            {
                "event_id": "foot-2",
                "datetime": "2026-09-09T05:20:00.000Z",
                "type": "Foot",
                "value": {"right": 12.4, "unit": "cm"},
            },
        ],
    }

    snap = parse_feed_payload(payload, tz=JST)
    assert snap.latest_by_type["Formula"].value is None
    assert snap.latest_by_type["BreastFeeding"].left_time is None
    assert snap.latest_by_type["BreastFeeding"].right_time is None
    assert snap.latest_by_type["BreastFeeding"].total_breastfeeding_time_sec is None
    assert snap.latest_by_type["BreastFeeding"].last_side is None
    assert snap.latest_by_type["BreastFeeding"].value is None

    assert snap.latest_by_type["Poop"].poop_color == "brown"
    assert snap.latest_by_type["Poop"].poop_amount is None
    assert snap.latest_by_type["Poop"].poop_hardness is None

    assert snap.latest_measurement_by_type["Temperature"].value == 36.8
    assert snap.latest_foot_left is not None and snap.latest_foot_left.foot_left == 12.3
    assert snap.latest_foot_right is not None and snap.latest_foot_right.foot_right == 12.4


def test_sleep_pairing_cross_midnight_and_ongoing_and_boundary() -> None:
    """Verify Sleep/WakeUp pairing, midnight clipping (JST), ongoing sleep, and boundaries."""
    # JST 2026-09-09 12:00 = UTC 2026-09-09 03:00
    # JST 2026-09-09 00:00 = UTC 2026-09-08 15:00
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-09T03:00:00.000Z",
        "range": {
            "from": "2026-09-08T03:00:00.000Z",
            "to": "2026-09-09T03:00:00.000Z",
        },
        "records": [
            # Orphan WakeUp at start of window -> partial boundary
            {
                "event_id": "w0",
                "datetime": "2026-09-08T04:00:00.000Z",
                "type": "WakeUp",
            },
            # Overnight sleep: JST 23:00 (UTC 14:00) -> JST 07:00 (UTC 22:00) = 480 min total, 420 min today
            {
                "event_id": "s1",
                "datetime": "2026-09-08T14:00:00.000Z",
                "type": "Sleep",
            },
            {
                "event_id": "w1",
                "datetime": "2026-09-08T22:00:00.000Z",
                "type": "WakeUp",
            },
            # Ongoing nap: JST 11:30 (UTC 02:30) -> now JST 12:00 (UTC 03:00) = 30 min
            {
                "event_id": "s2",
                "datetime": "2026-09-09T02:30:00.000Z",
                "type": "Sleep",
                "memo": "Morning nap",
            },
        ],
    }

    snap = parse_feed_payload(
        payload,
        tz=JST,
        now_utc=datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc),
    )

    assert snap.sleep.is_sleeping is True
    assert snap.sleep.is_partial_boundary is True
    assert snap.sleep.current_sleep_since == datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc)
    assert snap.sleep.last_sleep_duration_min == 480.0
    assert snap.sleep.today_sleep_min == 450.0  # 420 + 30
    assert snap.sleep.last_24h_sleep_min == 510.0  # 480 + 30
    assert snap.sleep.today_sessions_count == 2


def test_all_40_known_record_types_and_unknown_type_and_recent_limit() -> None:
    """Verify every official schema v1 type parses cleanly and unknown types are ignored."""
    base_ts = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
    records = []
    for idx, rtype in enumerate(sorted(KNOWN_RECORD_TYPES)):
        dt_str = datetime.fromtimestamp(
            base_ts.timestamp() + idx * 60, tz=timezone.utc
        ).isoformat()
        rec: dict = {
            "event_id": f"ev-{idx}",
            "datetime": dt_str,
            "type": rtype,
            "memo": f"memo-{idx} 🍼\nline2",
            "future_extra_field": {"ignored": True},
        }
        if rtype in ("Formula", "ExpressedBreastMilk", "Pumping", "Drink"):
            rec["value"] = {"value": 100, "unit": "ml"}
        elif rtype == "Temperature":
            rec["value"] = {"value": 36.5, "unit": "celsius"}
        elif rtype == "Weight":
            rec["value"] = {"value": 7.2, "unit": "kg"}
        elif rtype in ("Height", "Head", "Chest"):
            rec["value"] = {"value": 65.5, "unit": "cm"}
        elif rtype == "Foot":
            rec["value"] = {"left": 11.1, "right": 11.2, "unit": "cm"}
        elif rtype == "BreastFeeding":
            rec["leftTime"] = 180
            rec["rightTime"] = 120
            rec["last"] = "left"
            rec["value"] = {"value": 60, "unit": "ml"}
        elif rtype == "Poop":
            rec["details"] = {"amount": "small", "hardness": "normal", "color": "green"}
        records.append(rec)

    # Add an unknown future type
    records.append(
        {
            "event_id": "ev-future",
            "datetime": "2026-09-09T02:00:00.000Z",
            "type": "FutureRecordType",
            "unknown_nested": [1, 2, 3],
        }
    )

    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-09T03:00:00.000Z",
        "range": {
            "from": "2026-09-08T03:00:00.000Z",
            "to": "2026-09-09T03:00:00.000Z",
        },
        "records": records,
    }

    snap = parse_feed_payload(payload, tz=JST)
    assert len(KNOWN_RECORD_TYPES) == 40
    for rtype in KNOWN_RECORD_TYPES:
        assert rtype in snap.latest_by_type
        assert snap.counts_by_type[rtype] == 1

    assert snap.unknown_types == ("FutureRecordType",)
    assert len(snap.recent_records) == RECENT_RECORDS_LIMIT
    assert snap.recent_records[0]["event_id"] == "ev-future"
    assert snap.today_formula_ml == 100.0
    assert snap.today_expressed_milk_ml == 100.0
    assert snap.today_pumping_ml == 100.0
    assert snap.today_drink_ml == 100.0
    assert snap.today_breastfeeding_left_sec == 180
    assert snap.today_breastfeeding_right_sec == 120
    assert snap.today_breastfeeding_ml == 60.0
    assert snap.today_feeding_count == 3
    assert snap.today_total_milk_ml == 260.0


def test_intervals_clustering_and_next_estimates() -> None:
    """Verify interval averages, session clustering (mixed feeding / pee+poop), and estimates."""
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-09T12:00:00.000Z",
        "range": {
            "from": "2026-09-08T12:00:00.000Z",
            "to": "2026-09-09T12:00:00.000Z",
        },
        "records": [
            # Feeding session 1: 00:00 breast + 00:10 formula (clustered as 1 session starting 00:00)
            {"event_id": "bf1", "datetime": "2026-09-09T00:00:00.000Z", "type": "BreastFeeding"},
            {"event_id": "fm1", "datetime": "2026-09-09T00:10:00.000Z", "type": "Formula", "value": {"value": 80, "unit": "ml"}},
            # Feeding session 2: 03:00 (+180 min)
            {"event_id": "fm2", "datetime": "2026-09-09T03:00:00.000Z", "type": "Formula", "value": {"value": 100, "unit": "ml"}},
            # Feeding session 3: 06:00 (+180 min) -> avg = 180 min, next = 09:00
            {"event_id": "fm3", "datetime": "2026-09-09T06:00:00.000Z", "type": "Formula", "value": {"value": 120, "unit": "ml"}},
            # Diaper session 1: 01:00 pee + 01:02 poop (clustered as 1 diaper change)
            {"event_id": "p1", "datetime": "2026-09-09T01:00:00.000Z", "type": "Pee"},
            {"event_id": "po1", "datetime": "2026-09-09T01:02:00.000Z", "type": "Poop"},
            # Diaper session 2: 03:00 pee (+120 min)
            {"event_id": "p2", "datetime": "2026-09-09T03:00:00.000Z", "type": "Pee"},
            # Diaper session 3: 05:00 poop (+120 min) -> diaper avg = 120 min, next = 07:00
            {"event_id": "po2", "datetime": "2026-09-09T05:00:00.000Z", "type": "Poop"},
        ],
    }

    snap = parse_feed_payload(
        payload,
        tz=timezone.utc,
        now_utc=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert snap.feeding_interval.sample_count == 2
    assert snap.feeding_interval.avg_interval_min == 180.0
    assert snap.feeding_interval.next_expected_at == datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)

    assert snap.latest_diaper is not None
    assert snap.latest_diaper.event_id == "po2"
    assert snap.today_diaper_count == 3
    assert snap.diaper_interval.sample_count == 2
    assert snap.diaper_interval.avg_interval_min == 120.0
    assert snap.diaper_interval.next_expected_at == datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)

    assert snap.pee_interval.avg_interval_min == 120.0
    assert snap.poop_interval.avg_interval_min == 238.0  # 01:02 -> 05:00

    assert len(snap.recent_formula_records) == 3
    assert [r["value_ml"] for r in snap.recent_formula_records] == [120.0, 100.0, 80.0]


def test_day_night_sleep_split_and_multi_day_summaries() -> None:
    """Verify day (06-18 JST) vs night sleep split, yesterday totals, and 7-day averages."""
    # Now: JST 2026-09-10 21:00 = UTC 2026-09-10 12:00
    # Feed covers 3d: from JST 2026-09-07 21:00 (UTC 2026-09-07 12:00)
    # Complete past days: 2026-09-08 and 2026-09-09
    payload = {
        "schema_version": 1,
        "generated_at": "2026-09-10T12:00:00.000Z",
        "range": {
            "from": "2026-09-07T12:00:00.000Z",
            "to": "2026-09-10T12:00:00.000Z",
        },
        "records": [
            # 2026-09-08: Formula 500 ml (2 feeds)
            {"event_id": "d1-f1", "datetime": "2026-09-08T01:00:00.000Z", "type": "Formula", "value": {"value": 250, "unit": "ml"}},
            {"event_id": "d1-f2", "datetime": "2026-09-08T05:00:00.000Z", "type": "Formula", "value": {"value": 250, "unit": "ml"}},
            # 2026-09-09 (yesterday): Formula 700 ml + sleep 09:00-11:00 JST (day 120m) & 21:00-02:00 JST next day (night 180m on 09, 120m on 10)
            {"event_id": "d2-f1", "datetime": "2026-09-09T01:00:00.000Z", "type": "Formula", "value": {"value": 700, "unit": "ml"}},
            {"event_id": "d2-s1", "datetime": "2026-09-09T00:00:00.000Z", "type": "Sleep"},   # JST 09:00
            {"event_id": "d2-w1", "datetime": "2026-09-09T02:00:00.000Z", "type": "WakeUp"},  # JST 11:00
            {"event_id": "d2-s2", "datetime": "2026-09-09T12:00:00.000Z", "type": "Sleep"},   # JST 21:00
            {"event_id": "d3-w1", "datetime": "2026-09-09T17:00:00.000Z", "type": "WakeUp"},  # JST 02:00 on 09-10
            # 2026-09-10 (today): nap 13:00-14:30 JST (day 90m) + ongoing sleep from 20:00 JST (night 60m until 21:00)
            {"event_id": "d3-s2", "datetime": "2026-09-10T04:00:00.000Z", "type": "Sleep"},   # JST 13:00
            {"event_id": "d3-w2", "datetime": "2026-09-10T05:30:00.000Z", "type": "WakeUp"},  # JST 14:30
            {"event_id": "d3-s3", "datetime": "2026-09-10T11:00:00.000Z", "type": "Sleep"},   # JST 20:00 (ongoing)
        ],
    }

    snap = parse_feed_payload(
        payload,
        tz=JST,
        now_utc=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )

    # Today (2026-09-10 JST): day = 90m, night = 120m (00:00-02:00) + 60m (20:00-21:00) = 180m
    assert snap.sleep.today_day_sleep_min == 90.0
    assert snap.sleep.today_night_sleep_min == 180.0
    assert snap.sleep.today_sleep_min == 270.0

    # Yesterday (2026-09-09 JST): day = 120m, night = 180m (21:00-24:00), total = 300m
    assert snap.sleep.yesterday_complete is True
    assert snap.sleep.yesterday_day_sleep_min == 120.0
    assert snap.sleep.yesterday_night_sleep_min == 180.0
    assert snap.sleep.yesterday_sleep_min == 300.0

    assert len(snap.sleep.sessions) == 4
    assert snap.sleep.sessions[-1].ongoing is True
    assert snap.sleep.sessions[-1].duration_min == 60.0

    # Complete past days: 09-08 (500 ml, 0 sleep) and 09-09 (700 ml, 300m sleep) -> avg formula = 600 ml, avg sleep = 150m
    assert snap.avg_7d_formula_ml == 600.0
    assert snap.avg_7d_sleep_min == 150.0
    assert snap.yesterday_summary is not None
    assert snap.yesterday_summary.formula_ml == 700.0
    assert snap.day_before_yesterday_summary is not None
    assert snap.day_before_yesterday_summary.formula_ml == 500.0
    assert snap.day_before_yesterday_summary.formula_avg_ml == 250.0
