"""Pure-Python parser and aggregator for PiyoLog Data Feed JSON (schema v1)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from .const import (
    FEEDING_TYPES,
    KNOWN_RECORD_TYPES,
    MEASUREMENT_TYPES,
    RECENT_RECORDS_LIMIT,
    TYPE_BREAST_FEEDING,
    TYPE_DRINK,
    TYPE_EXPRESSED_BREAST_MILK,
    TYPE_FOOT,
    TYPE_FORMULA,
    TYPE_POOP,
    TYPE_PUMPING,
    TYPE_SLEEP,
    TYPE_WAKE_UP,
)


def parse_utc_datetime(raw: str) -> datetime:
    """Parse an ISO 8601 datetime string into a UTC-aware datetime."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _positive_float(val: Any) -> float | None:
    """Return a positive float, or None if omitted/invalid/non-positive."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    fval = float(val)
    if fval <= 0:
        return None
    return fval


def _positive_int(val: Any) -> int | None:
    """Return a positive int, or None if omitted/invalid/non-positive."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    ival = int(val)
    if ival <= 0:
        return None
    return ival


def _non_empty_str(val: Any) -> str | None:
    """Return string if non-empty, else None (preserving content/whitespace)."""
    if isinstance(val, str) and val != "":
        return val
    return None


@dataclass(frozen=True)
class ParsedRecord:
    """Normalized representation of a single PiyoLog record."""

    event_id: str
    dt: datetime
    record_type: str
    value: float | None = None
    unit: str | None = None
    left_time: int | None = None
    right_time: int | None = None
    last_side: str | None = None
    foot_left: float | None = None
    foot_right: float | None = None
    poop_amount: str | None = None
    poop_hardness: str | None = None
    poop_color: str | None = None
    memo: str | None = None

    @property
    def total_breastfeeding_time_sec(self) -> int | None:
        """Return sum of left and right breastfeeding seconds if at least one is set."""
        if self.left_time is None and self.right_time is None:
            return None
        return (self.left_time or 0) + (self.right_time or 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a compact JSON-friendly dict without omitted fields."""
        data: dict[str, Any] = {
            "event_id": self.event_id,
            "datetime": self.dt.isoformat(),
            "type": self.record_type,
        }
        if self.value is not None:
            data["value"] = self.value
            if self.unit is not None:
                data["unit"] = self.unit
        if self.last_side is not None:
            data["last"] = self.last_side
        if self.left_time is not None:
            data["left_time_sec"] = self.left_time
        if self.right_time is not None:
            data["right_time_sec"] = self.right_time
        if self.foot_left is not None or self.foot_right is not None:
            foot: dict[str, Any] = {"unit": self.unit or "cm"}
            if self.foot_left is not None:
                foot["left"] = self.foot_left
            if self.foot_right is not None:
                foot["right"] = self.foot_right
            data["foot"] = foot
        if (
            self.poop_amount is not None
            or self.poop_hardness is not None
            or self.poop_color is not None
        ):
            details: dict[str, str] = {}
            if self.poop_amount is not None:
                details["amount"] = self.poop_amount
            if self.poop_hardness is not None:
                details["hardness"] = self.poop_hardness
            if self.poop_color is not None:
                details["color"] = self.poop_color
            data["details"] = details
        if self.memo is not None:
            data["memo"] = self.memo
        return data


@dataclass(frozen=True)
class SleepSummary:
    """Aggregated sleep state and durations derived from Sleep/WakeUp records."""

    is_sleeping: bool | None
    current_sleep_since: datetime | None
    last_sleep_event_type: str | None
    last_sleep_duration_min: float | None
    today_sleep_min: float
    last_24h_sleep_min: float
    today_sessions_count: int
    is_partial_boundary: bool


@dataclass(frozen=True)
class PiyoLogSnapshot:
    """Parsed and aggregated snapshot of a PiyoLog Data Feed response."""

    schema_version: int
    generated_at: datetime
    range_from: datetime
    range_to: datetime
    range_covers_today: bool
    records: tuple[ParsedRecord, ...]
    latest_record: ParsedRecord | None
    latest_by_type: dict[str, ParsedRecord]
    latest_feeding: ParsedRecord | None
    latest_measurement_by_type: dict[str, ParsedRecord]
    latest_foot_left: ParsedRecord | None
    latest_foot_right: ParsedRecord | None
    counts_by_type: dict[str, int]
    today_counts_by_type: dict[str, int]
    last_24h_counts_by_type: dict[str, int]
    unknown_types: tuple[str, ...]
    today_formula_ml: float
    last_24h_formula_ml: float
    today_expressed_milk_ml: float
    last_24h_expressed_milk_ml: float
    today_pumping_ml: float
    last_24h_pumping_ml: float
    today_drink_ml: float
    last_24h_drink_ml: float
    today_breastfeeding_left_sec: int
    today_breastfeeding_right_sec: int
    today_breastfeeding_ml: float
    last_24h_breastfeeding_left_sec: int
    last_24h_breastfeeding_right_sec: int
    last_24h_breastfeeding_ml: float
    today_feeding_count: int
    last_24h_feeding_count: int
    today_total_milk_ml: float
    sleep: SleepSummary
    recent_records: tuple[dict[str, Any], ...]


def _parse_single_record(raw: Any) -> ParsedRecord | None:
    """Parse one raw JSON record dict; return None if structurally invalid."""
    if not isinstance(raw, dict):
        return None

    event_id = raw.get("event_id")
    raw_dt = raw.get("datetime")
    record_type = raw.get("type")

    if (
        not isinstance(event_id, str)
        or not event_id
        or not isinstance(raw_dt, str)
        or not isinstance(record_type, str)
        or not record_type
    ):
        return None

    try:
        dt = parse_utc_datetime(raw_dt)
    except ValueError:
        return None

    value: float | None = None
    unit: str | None = None
    foot_left: float | None = None
    foot_right: float | None = None

    raw_value = raw.get("value")
    if isinstance(raw_value, dict):
        unit = _non_empty_str(raw_value.get("unit"))
        if record_type == TYPE_FOOT:
            foot_left = _positive_float(raw_value.get("left"))
            foot_right = _positive_float(raw_value.get("right"))
        else:
            value = _positive_float(raw_value.get("value"))

    left_time: int | None = None
    right_time: int | None = None
    last_side: str | None = None
    if record_type == TYPE_BREAST_FEEDING:
        left_time = _positive_int(raw.get("leftTime"))
        right_time = _positive_int(raw.get("rightTime"))
        raw_last = raw.get("last")
        if raw_last in ("left", "right"):
            last_side = raw_last

    poop_amount: str | None = None
    poop_hardness: str | None = None
    poop_color: str | None = None
    if record_type == TYPE_POOP:
        details = raw.get("details")
        if isinstance(details, dict):
            poop_amount = _non_empty_str(details.get("amount"))
            poop_hardness = _non_empty_str(details.get("hardness"))
            poop_color = _non_empty_str(details.get("color"))

    memo = _non_empty_str(raw.get("memo"))

    return ParsedRecord(
        event_id=event_id,
        dt=dt,
        record_type=record_type,
        value=value,
        unit=unit,
        left_time=left_time,
        right_time=right_time,
        last_side=last_side,
        foot_left=foot_left,
        foot_right=foot_right,
        poop_amount=poop_amount,
        poop_hardness=poop_hardness,
        poop_color=poop_color,
        memo=memo,
    )


def _overlap_seconds(
    start: datetime,
    end: datetime,
    win_start: datetime,
    win_end: datetime,
) -> float:
    """Return overlap duration in seconds between [start, end] and [win_start, win_end]."""
    s = max(start, win_start)
    e = min(end, win_end)
    if e <= s:
        return 0.0
    return (e - s).total_seconds()


def _compute_sleep_summary(
    records: list[ParsedRecord],
    *,
    today_start_utc: datetime,
    window_24h_start_utc: datetime,
    effective_now_utc: datetime,
) -> SleepSummary:
    """Pair Sleep and WakeUp records safely, handling range boundaries."""
    sleep_events = [
        r for r in records if r.record_type in (TYPE_SLEEP, TYPE_WAKE_UP)
    ]
    if not sleep_events:
        return SleepSummary(
            is_sleeping=None,
            current_sleep_since=None,
            last_sleep_event_type=None,
            last_sleep_duration_min=None,
            today_sleep_min=0.0,
            last_24h_sleep_min=0.0,
            today_sessions_count=0,
            is_partial_boundary=False,
        )

    # Ensure deterministic order when Sleep and WakeUp share the exact timestamp:
    # WakeUp ends a prior session before a new Sleep starts.
    sleep_events.sort(
        key=lambda r: (r.dt, 0 if r.record_type == TYPE_WAKE_UP else 1)
    )

    sessions: list[tuple[datetime, datetime]] = []
    open_sleep: datetime | None = None
    is_partial_boundary = False

    for rec in sleep_events:
        if rec.record_type == TYPE_SLEEP:
            if open_sleep is not None:
                # Duplicate Sleep without WakeUp (missed WakeUp entry)
                is_partial_boundary = True
            open_sleep = rec.dt
        else:  # WakeUp
            if open_sleep is None:
                # WakeUp before any Sleep in the feed window
                is_partial_boundary = True
            else:
                if rec.dt >= open_sleep:
                    sessions.append((open_sleep, rec.dt))
                open_sleep = None

    last_event = sleep_events[-1]
    is_sleeping = last_event.record_type == TYPE_SLEEP
    current_sleep_since = open_sleep if is_sleeping else None

    last_sleep_duration_min: float | None = None
    if sessions:
        last_s, last_e = sessions[-1]
        last_sleep_duration_min = round((last_e - last_s).total_seconds() / 60.0, 1)

    all_intervals = list(sessions)
    if is_sleeping and current_sleep_since is not None and effective_now_utc > current_sleep_since:
        all_intervals.append((current_sleep_since, effective_now_utc))

    today_sec = 0.0
    last_24h_sec = 0.0
    today_sessions_count = 0

    for start, end in all_intervals:
        t_ov = _overlap_seconds(start, end, today_start_utc, effective_now_utc)
        if t_ov > 0:
            today_sec += t_ov
            today_sessions_count += 1
        last_24h_sec += _overlap_seconds(
            start, end, window_24h_start_utc, effective_now_utc
        )

    return SleepSummary(
        is_sleeping=is_sleeping,
        current_sleep_since=current_sleep_since,
        last_sleep_event_type=last_event.record_type,
        last_sleep_duration_min=last_sleep_duration_min,
        today_sleep_min=round(today_sec / 60.0, 1),
        last_24h_sleep_min=round(last_24h_sec / 60.0, 1),
        today_sessions_count=today_sessions_count,
        is_partial_boundary=is_partial_boundary,
    )


def parse_feed_payload(
    payload: dict[str, Any],
    *,
    tz: tzinfo = timezone.utc,
    now_utc: datetime | None = None,
) -> PiyoLogSnapshot:
    """Parse and aggregate a validated PiyoLog Data Feed v1 JSON payload."""
    schema_version = int(payload["schema_version"])
    generated_at = parse_utc_datetime(payload["generated_at"])
    range_from = parse_utc_datetime(payload["range"]["from"])
    range_to = parse_utc_datetime(payload["range"]["to"])

    effective_now = now_utc.astimezone(timezone.utc) if now_utc else generated_at
    if effective_now < generated_at:
        effective_now = generated_at

    local_now = effective_now.astimezone(tz)
    local_today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = local_today_start.astimezone(timezone.utc)
    window_24h_start_utc = effective_now - timedelta(hours=24)
    range_covers_today = range_from <= today_start_utc

    parsed_records: list[ParsedRecord] = []
    unknown_types_set: set[str] = set()

    for raw_rec in payload.get("records", []):
        rec = _parse_single_record(raw_rec)
        if rec is None:
            continue
        parsed_records.append(rec)
        if rec.record_type not in KNOWN_RECORD_TYPES:
            unknown_types_set.add(rec.record_type)

    # Spec guarantees ascending datetime, but sort stably as a safeguard
    parsed_records.sort(key=lambda r: r.dt)

    latest_by_type: dict[str, ParsedRecord] = {}
    latest_measurement_by_type: dict[str, ParsedRecord] = {}
    latest_feeding: ParsedRecord | None = None
    latest_foot_left: ParsedRecord | None = None
    latest_foot_right: ParsedRecord | None = None

    counts_by_type: Counter[str] = Counter()
    today_counts_by_type: Counter[str] = Counter()
    last_24h_counts_by_type: Counter[str] = Counter()

    today_formula_ml = 0.0
    last_24h_formula_ml = 0.0
    today_expressed_milk_ml = 0.0
    last_24h_expressed_milk_ml = 0.0
    today_pumping_ml = 0.0
    last_24h_pumping_ml = 0.0
    today_drink_ml = 0.0
    last_24h_drink_ml = 0.0

    today_bf_left_sec = 0
    today_bf_right_sec = 0
    today_bf_ml = 0.0
    last_24h_bf_left_sec = 0
    last_24h_bf_right_sec = 0
    last_24h_bf_ml = 0.0

    today_feeding_count = 0
    last_24h_feeding_count = 0

    for rec in parsed_records:
        rtype = rec.record_type
        counts_by_type[rtype] += 1
        latest_by_type[rtype] = rec

        if rtype in FEEDING_TYPES:
            latest_feeding = rec

        if rtype in MEASUREMENT_TYPES and rec.value is not None:
            latest_measurement_by_type[rtype] = rec

        if rtype == TYPE_FOOT:
            if rec.foot_left is not None:
                latest_foot_left = rec
            if rec.foot_right is not None:
                latest_foot_right = rec

        is_today = rec.dt >= today_start_utc
        is_last_24h = rec.dt >= window_24h_start_utc

        if is_today:
            today_counts_by_type[rtype] += 1
            if rtype in FEEDING_TYPES:
                today_feeding_count += 1
            if rtype == TYPE_FORMULA and rec.value is not None:
                today_formula_ml += rec.value
            elif rtype == TYPE_EXPRESSED_BREAST_MILK and rec.value is not None:
                today_expressed_milk_ml += rec.value
            elif rtype == TYPE_PUMPING and rec.value is not None:
                today_pumping_ml += rec.value
            elif rtype == TYPE_DRINK and rec.value is not None:
                today_drink_ml += rec.value
            elif rtype == TYPE_BREAST_FEEDING:
                if rec.left_time is not None:
                    today_bf_left_sec += rec.left_time
                if rec.right_time is not None:
                    today_bf_right_sec += rec.right_time
                if rec.value is not None:
                    today_bf_ml += rec.value

        if is_last_24h:
            last_24h_counts_by_type[rtype] += 1
            if rtype in FEEDING_TYPES:
                last_24h_feeding_count += 1
            if rtype == TYPE_FORMULA and rec.value is not None:
                last_24h_formula_ml += rec.value
            elif rtype == TYPE_EXPRESSED_BREAST_MILK and rec.value is not None:
                last_24h_expressed_milk_ml += rec.value
            elif rtype == TYPE_PUMPING and rec.value is not None:
                last_24h_pumping_ml += rec.value
            elif rtype == TYPE_DRINK and rec.value is not None:
                last_24h_drink_ml += rec.value
            elif rtype == TYPE_BREAST_FEEDING:
                if rec.left_time is not None:
                    last_24h_bf_left_sec += rec.left_time
                if rec.right_time is not None:
                    last_24h_bf_right_sec += rec.right_time
                if rec.value is not None:
                    last_24h_bf_ml += rec.value

    sleep_summary = _compute_sleep_summary(
        parsed_records,
        today_start_utc=today_start_utc,
        window_24h_start_utc=window_24h_start_utc,
        effective_now_utc=effective_now,
    )

    recent_records = tuple(
        rec.to_dict() for rec in reversed(parsed_records[-RECENT_RECORDS_LIMIT:])
    )

    today_total_milk_ml = round(
        today_formula_ml + today_expressed_milk_ml + today_bf_ml, 1
    )

    return PiyoLogSnapshot(
        schema_version=schema_version,
        generated_at=generated_at,
        range_from=range_from,
        range_to=range_to,
        range_covers_today=range_covers_today,
        records=tuple(parsed_records),
        latest_record=parsed_records[-1] if parsed_records else None,
        latest_by_type=latest_by_type,
        latest_feeding=latest_feeding,
        latest_measurement_by_type=latest_measurement_by_type,
        latest_foot_left=latest_foot_left,
        latest_foot_right=latest_foot_right,
        counts_by_type=dict(counts_by_type),
        today_counts_by_type=dict(today_counts_by_type),
        last_24h_counts_by_type=dict(last_24h_counts_by_type),
        unknown_types=tuple(sorted(unknown_types_set)),
        today_formula_ml=round(today_formula_ml, 1),
        last_24h_formula_ml=round(last_24h_formula_ml, 1),
        today_expressed_milk_ml=round(today_expressed_milk_ml, 1),
        last_24h_expressed_milk_ml=round(last_24h_expressed_milk_ml, 1),
        today_pumping_ml=round(today_pumping_ml, 1),
        last_24h_pumping_ml=round(last_24h_pumping_ml, 1),
        today_drink_ml=round(today_drink_ml, 1),
        last_24h_drink_ml=round(last_24h_drink_ml, 1),
        today_breastfeeding_left_sec=today_bf_left_sec,
        today_breastfeeding_right_sec=today_bf_right_sec,
        today_breastfeeding_ml=round(today_bf_ml, 1),
        last_24h_breastfeeding_left_sec=last_24h_bf_left_sec,
        last_24h_breastfeeding_right_sec=last_24h_bf_right_sec,
        last_24h_breastfeeding_ml=round(last_24h_bf_ml, 1),
        today_feeding_count=today_feeding_count,
        last_24h_feeding_count=last_24h_feeding_count,
        today_total_milk_ml=today_total_milk_ml,
        sleep=sleep_summary,
        recent_records=recent_records,
    )
