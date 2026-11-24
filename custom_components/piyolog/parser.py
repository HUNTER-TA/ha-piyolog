"""Pure-Python parser and aggregator for PiyoLog Data Feed JSON (schema v1)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .const import (
    DAYTIME_END_HOUR,
    DAYTIME_START_HOUR,
    DIAPER_CLUSTER_MINUTES,
    DIAPER_TYPES,
    FEEDING_CLUSTER_MINUTES,
    FEEDING_TYPES,
    KNOWN_RECORD_TYPES,
    MEASUREMENT_TYPES,
    RECENT_FORMULA_LIMIT,
    RECENT_RECORDS_LIMIT,
    TYPE_BREAST_FEEDING,
    TYPE_DRINK,
    TYPE_EXPRESSED_BREAST_MILK,
    TYPE_FOOT,
    TYPE_FORMULA,
    TYPE_PEE,
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
class IntervalStats:
    """Average interval and estimated next occurrence for an event category."""

    last_at: datetime | None
    avg_interval_min: float | None
    next_expected_at: datetime | None
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_at": self.last_at.isoformat() if self.last_at else None,
            "avg_interval_min": self.avg_interval_min,
            "next_expected_at": (
                self.next_expected_at.isoformat() if self.next_expected_at else None
            ),
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class SleepSession:
    """A single paired (or ongoing) sleep interval."""

    start: datetime
    end: datetime
    ongoing: bool
    duration_min: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "ongoing": self.ongoing,
            "duration_min": self.duration_min,
        }


@dataclass(frozen=True)
class SleepSummary:
    """Aggregated sleep state and durations derived from Sleep/WakeUp records."""

    is_sleeping: bool | None
    current_sleep_since: datetime | None
    last_sleep_event_type: str | None
    last_sleep_duration_min: float | None
    today_sleep_min: float
    today_day_sleep_min: float
    today_night_sleep_min: float
    yesterday_sleep_min: float
    yesterday_day_sleep_min: float
    yesterday_night_sleep_min: float
    yesterday_complete: bool
    last_24h_sleep_min: float
    today_sessions_count: int
    is_partial_boundary: bool
    sessions: tuple[SleepSession, ...]


@dataclass(frozen=True)
class DailySummary:
    """Per-local-day aggregated totals for charts and historical comparisons."""

    day: str  # YYYY-MM-DD in local timezone
    is_complete: bool
    formula_ml: float
    formula_count: int
    formula_avg_ml: float | None
    expressed_milk_ml: float
    breastfeeding_count: int
    breastfeeding_ml: float
    feeding_count: int
    total_milk_ml: float
    pee_count: int
    poop_count: int
    diaper_count: int
    sleep_min: float
    day_sleep_min: float
    night_sleep_min: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day,
            "is_complete": self.is_complete,
            "formula_ml": self.formula_ml,
            "formula_count": self.formula_count,
            "formula_avg_ml": self.formula_avg_ml,
            "expressed_milk_ml": self.expressed_milk_ml,
            "breastfeeding_count": self.breastfeeding_count,
            "breastfeeding_ml": self.breastfeeding_ml,
            "feeding_count": self.feeding_count,
            "total_milk_ml": self.total_milk_ml,
            "pee_count": self.pee_count,
            "poop_count": self.poop_count,
            "diaper_count": self.diaper_count,
            "sleep_min": self.sleep_min,
            "day_sleep_min": self.day_sleep_min,
            "night_sleep_min": self.night_sleep_min,
        }


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
    latest_diaper: ParsedRecord | None
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
    today_diaper_count: int
    last_24h_diaper_count: int
    today_total_milk_ml: float
    sleep: SleepSummary
    feeding_interval: IntervalStats
    diaper_interval: IntervalStats
    pee_interval: IntervalStats
    poop_interval: IntervalStats
    recent_formula_records: tuple[dict[str, Any], ...]
    daily_summaries: tuple[DailySummary, ...]
    yesterday_summary: DailySummary | None
    day_before_yesterday_summary: DailySummary | None
    avg_7d_formula_ml: float | None
    avg_7d_diaper_count: float | None
    avg_7d_sleep_min: float | None
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


def _day_bounds_utc(target_day: date, tz: tzinfo) -> tuple[datetime, datetime, datetime, datetime]:
    """Return UTC bounds for a local day: (day_start, daytime_start, daytime_end, day_end)."""
    local_start = datetime.combine(target_day, time(0, 0), tzinfo=tz)
    local_daytime_start = datetime.combine(target_day, time(DAYTIME_START_HOUR, 0), tzinfo=tz)
    local_daytime_end = datetime.combine(target_day, time(DAYTIME_END_HOUR, 0), tzinfo=tz)
    local_end = datetime.combine(target_day + timedelta(days=1), time(0, 0), tzinfo=tz)
    return (
        local_start.astimezone(timezone.utc),
        local_daytime_start.astimezone(timezone.utc),
        local_daytime_end.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
    )


def _day_night_sleep_minutes(
    intervals: list[tuple[datetime, datetime]],
    target_day: date,
    tz: tzinfo,
    cap_utc: datetime,
) -> tuple[float, float, float, int]:
    """Compute (total_min, day_min, night_min, sessions_count) for a local calendar day."""
    d_start, dt_start, dt_end, d_end = _day_bounds_utc(target_day, tz)
    eff_end = min(d_end, cap_utc)
    if eff_end <= d_start:
        return 0.0, 0.0, 0.0, 0

    total_sec = 0.0
    day_sec = 0.0
    count = 0
    for start, end in intervals:
        ov = _overlap_seconds(start, end, d_start, eff_end)
        if ov > 0:
            total_sec += ov
            count += 1
            day_sec += _overlap_seconds(start, end, dt_start, min(dt_end, eff_end))

    night_sec = max(0.0, total_sec - day_sec)
    total_min = round(total_sec / 60.0, 1)
    day_min = round(day_sec / 60.0, 1)
    night_min = round(night_sec / 60.0, 1)
    return total_min, day_min, night_min, count


def _compute_interval_stats(
    timestamps: list[datetime],
    *,
    cluster_minutes: int = 0,
) -> IntervalStats:
    """Compute average interval and next expected timestamp from ascending UTC datetimes.

    Events within `cluster_minutes` of the previous event are merged into one
    logical care session so simultaneous logs (e.g., pee+poop or breast+formula)
    do not skew the average interval toward 0.
    """
    if not timestamps:
        return IntervalStats(
            last_at=None,
            avg_interval_min=None,
            next_expected_at=None,
            sample_count=0,
        )

    ordered = sorted(timestamps)
    last_at = ordered[-1]

    if cluster_minutes > 0:
        threshold = timedelta(minutes=cluster_minutes)
        session_starts: list[datetime] = [ordered[0]]
        prev_raw = ordered[0]
        for ts in ordered[1:]:
            if ts - prev_raw > threshold:
                session_starts.append(ts)
            prev_raw = ts
    else:
        session_starts = ordered

    if len(session_starts) < 2:
        return IntervalStats(
            last_at=last_at,
            avg_interval_min=None,
            next_expected_at=None,
            sample_count=0,
        )

    gaps_sec = [
        (session_starts[i] - session_starts[i - 1]).total_seconds()
        for i in range(1, len(session_starts))
    ]
    avg_sec = sum(gaps_sec) / len(gaps_sec)
    avg_min = round(avg_sec / 60.0, 1)
    next_expected = last_at + timedelta(seconds=round(avg_sec))

    return IntervalStats(
        last_at=last_at,
        avg_interval_min=avg_min,
        next_expected_at=next_expected,
        sample_count=len(gaps_sec),
    )


def _compute_sleep_summary(
    records: list[ParsedRecord],
    *,
    tz: tzinfo,
    local_today: date,
    range_from_utc: datetime,
    window_24h_start_utc: datetime,
    effective_now_utc: datetime,
) -> tuple[SleepSummary, list[tuple[datetime, datetime]]]:
    """Pair Sleep and WakeUp records safely, handling range boundaries and day/night split."""
    yesterday = local_today - timedelta(days=1)
    y_start_utc, _, _, _ = _day_bounds_utc(yesterday, tz)
    yesterday_complete = range_from_utc <= y_start_utc

    sleep_events = [
        r for r in records if r.record_type in (TYPE_SLEEP, TYPE_WAKE_UP)
    ]
    if not sleep_events:
        return (
            SleepSummary(
                is_sleeping=None,
                current_sleep_since=None,
                last_sleep_event_type=None,
                last_sleep_duration_min=None,
                today_sleep_min=0.0,
                today_day_sleep_min=0.0,
                today_night_sleep_min=0.0,
                yesterday_sleep_min=0.0,
                yesterday_day_sleep_min=0.0,
                yesterday_night_sleep_min=0.0,
                yesterday_complete=yesterday_complete,
                last_24h_sleep_min=0.0,
                today_sessions_count=0,
                is_partial_boundary=False,
                sessions=(),
            ),
            [],
        )

    # Ensure deterministic order when Sleep and WakeUp share the exact timestamp:
    # WakeUp ends a prior session before a new Sleep starts.
    sleep_events.sort(
        key=lambda r: (r.dt, 0 if r.record_type == TYPE_WAKE_UP else 1)
    )

    completed_intervals: list[tuple[datetime, datetime]] = []
    open_sleep: datetime | None = None
    is_partial_boundary = False

    for rec in sleep_events:
        if rec.record_type == TYPE_SLEEP:
            if open_sleep is not None:
                is_partial_boundary = True
            open_sleep = rec.dt
        else:  # WakeUp
            if open_sleep is None:
                is_partial_boundary = True
            else:
                if rec.dt >= open_sleep:
                    completed_intervals.append((open_sleep, rec.dt))
                open_sleep = None

    last_event = sleep_events[-1]
    is_sleeping = last_event.record_type == TYPE_SLEEP
    current_sleep_since = open_sleep if is_sleeping else None

    last_sleep_duration_min: float | None = None
    if completed_intervals:
        last_s, last_e = completed_intervals[-1]
        last_sleep_duration_min = round((last_e - last_s).total_seconds() / 60.0, 1)

    all_intervals = list(completed_intervals)
    session_objs: list[SleepSession] = [
        SleepSession(
            start=s,
            end=e,
            ongoing=False,
            duration_min=round((e - s).total_seconds() / 60.0, 1),
        )
        for s, e in completed_intervals
    ]
    if is_sleeping and current_sleep_since is not None and effective_now_utc > current_sleep_since:
        all_intervals.append((current_sleep_since, effective_now_utc))
        session_objs.append(
            SleepSession(
                start=current_sleep_since,
                end=effective_now_utc,
                ongoing=True,
                duration_min=round(
                    (effective_now_utc - current_sleep_since).total_seconds() / 60.0, 1
                ),
            )
        )

    today_min, today_day_min, today_night_min, today_sessions_count = (
        _day_night_sleep_minutes(all_intervals, local_today, tz, effective_now_utc)
    )
    y_min, y_day_min, y_night_min, _ = _day_night_sleep_minutes(
        all_intervals, yesterday, tz, effective_now_utc
    )

    last_24h_sec = sum(
        _overlap_seconds(s, e, window_24h_start_utc, effective_now_utc)
        for s, e in all_intervals
    )

    return (
        SleepSummary(
            is_sleeping=is_sleeping,
            current_sleep_since=current_sleep_since,
            last_sleep_event_type=last_event.record_type,
            last_sleep_duration_min=last_sleep_duration_min,
            today_sleep_min=today_min,
            today_day_sleep_min=today_day_min,
            today_night_sleep_min=today_night_min,
            yesterday_sleep_min=y_min,
            yesterday_day_sleep_min=y_day_min,
            yesterday_night_sleep_min=y_night_min,
            yesterday_complete=yesterday_complete,
            last_24h_sleep_min=round(last_24h_sec / 60.0, 1),
            today_sessions_count=today_sessions_count,
            is_partial_boundary=is_partial_boundary,
            sessions=tuple(session_objs),
        ),
        all_intervals,
    )


def _compute_daily_summaries(
    records: list[ParsedRecord],
    sleep_intervals: list[tuple[datetime, datetime]],
    *,
    tz: tzinfo,
    range_from_utc: datetime,
    effective_now_utc: datetime,
) -> tuple[DailySummary, ...]:
    """Build per-local-day summaries covering [range_from, effective_now]."""
    start_day = range_from_utc.astimezone(tz).date()
    end_day = effective_now_utc.astimezone(tz).date()

    records_by_day: dict[date, list[ParsedRecord]] = {}
    for rec in records:
        rec_day = rec.dt.astimezone(tz).date()
        records_by_day.setdefault(rec_day, []).append(rec)

    summaries: list[DailySummary] = []
    cur = start_day
    while cur <= end_day:
        d_start_utc, _, _, _ = _day_bounds_utc(cur, tz)
        is_complete = range_from_utc <= d_start_utc
        day_recs = records_by_day.get(cur, [])

        formula_ml = 0.0
        formula_count = 0
        formula_valued_count = 0
        expressed_milk_ml = 0.0
        bf_count = 0
        bf_ml = 0.0
        feeding_count = 0
        pee_count = 0
        poop_count = 0
        diaper_ts: list[datetime] = []

        for rec in day_recs:
            rt = rec.record_type
            if rt in FEEDING_TYPES:
                feeding_count += 1
            if rt == TYPE_FORMULA:
                formula_count += 1
                if rec.value is not None:
                    formula_ml += rec.value
                    formula_valued_count += 1
            elif rt == TYPE_EXPRESSED_BREAST_MILK:
                if rec.value is not None:
                    expressed_milk_ml += rec.value
            elif rt == TYPE_BREAST_FEEDING:
                bf_count += 1
                if rec.value is not None:
                    bf_ml += rec.value
            elif rt == TYPE_PEE:
                pee_count += 1
                diaper_ts.append(rec.dt)
            elif rt == TYPE_POOP:
                poop_count += 1
                diaper_ts.append(rec.dt)

        # Count clustered diaper changes (pee+poop within threshold count as 1 change)
        diaper_count = 0
        if diaper_ts:
            diaper_ts.sort()
            diaper_count = 1
            prev_ts = diaper_ts[0]
            threshold = timedelta(minutes=DIAPER_CLUSTER_MINUTES)
            for ts in diaper_ts[1:]:
                if ts - prev_ts > threshold:
                    diaper_count += 1
                prev_ts = ts

        sleep_min, day_sleep_min, night_sleep_min, _ = _day_night_sleep_minutes(
            sleep_intervals, cur, tz, effective_now_utc
        )

        formula_avg_ml = (
            round(formula_ml / formula_valued_count, 1)
            if formula_valued_count > 0
            else None
        )
        total_milk_ml = round(formula_ml + expressed_milk_ml + bf_ml, 1)

        summaries.append(
            DailySummary(
                day=cur.isoformat(),
                is_complete=is_complete,
                formula_ml=round(formula_ml, 1),
                formula_count=formula_count,
                formula_avg_ml=formula_avg_ml,
                expressed_milk_ml=round(expressed_milk_ml, 1),
                breastfeeding_count=bf_count,
                breastfeeding_ml=round(bf_ml, 1),
                feeding_count=feeding_count,
                total_milk_ml=total_milk_ml,
                pee_count=pee_count,
                poop_count=poop_count,
                diaper_count=diaper_count,
                sleep_min=sleep_min,
                day_sleep_min=day_sleep_min,
                night_sleep_min=night_sleep_min,
            )
        )
        cur += timedelta(days=1)

    return tuple(summaries)


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
    local_today = local_now.date()
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
    latest_diaper: ParsedRecord | None = None
    latest_foot_left: ParsedRecord | None = None
    latest_foot_right: ParsedRecord | None = None

    counts_by_type: Counter[str] = Counter()
    today_counts_by_type: Counter[str] = Counter()
    last_24h_counts_by_type: Counter[str] = Counter()

    feeding_ts: list[datetime] = []
    diaper_ts: list[datetime] = []
    pee_ts: list[datetime] = []
    poop_ts: list[datetime] = []
    formula_records: list[ParsedRecord] = []

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
    today_diaper_ts: list[datetime] = []
    last_24h_diaper_ts: list[datetime] = []

    for rec in parsed_records:
        rtype = rec.record_type
        counts_by_type[rtype] += 1
        latest_by_type[rtype] = rec

        if rtype in FEEDING_TYPES:
            latest_feeding = rec
            feeding_ts.append(rec.dt)

        if rtype in DIAPER_TYPES:
            latest_diaper = rec
            diaper_ts.append(rec.dt)

        if rtype == TYPE_PEE:
            pee_ts.append(rec.dt)
        elif rtype == TYPE_POOP:
            poop_ts.append(rec.dt)
        elif rtype == TYPE_FORMULA:
            formula_records.append(rec)

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
            if rtype in DIAPER_TYPES:
                today_diaper_ts.append(rec.dt)
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
            if rtype in DIAPER_TYPES:
                last_24h_diaper_ts.append(rec.dt)
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

    def _count_clustered(ts_list: list[datetime], threshold_min: int) -> int:
        if not ts_list:
            return 0
        count = 1
        prev = ts_list[0]
        delta = timedelta(minutes=threshold_min)
        for ts in ts_list[1:]:
            if ts - prev > delta:
                count += 1
            prev = ts
        return count

    today_diaper_count = _count_clustered(today_diaper_ts, DIAPER_CLUSTER_MINUTES)
    last_24h_diaper_count = _count_clustered(last_24h_diaper_ts, DIAPER_CLUSTER_MINUTES)

    sleep_summary, sleep_intervals = _compute_sleep_summary(
        parsed_records,
        tz=tz,
        local_today=local_today,
        range_from_utc=range_from,
        window_24h_start_utc=window_24h_start_utc,
        effective_now_utc=effective_now,
    )

    feeding_interval = _compute_interval_stats(
        feeding_ts, cluster_minutes=FEEDING_CLUSTER_MINUTES
    )
    diaper_interval = _compute_interval_stats(
        diaper_ts, cluster_minutes=DIAPER_CLUSTER_MINUTES
    )
    pee_interval = _compute_interval_stats(pee_ts)
    poop_interval = _compute_interval_stats(poop_ts)

    recent_formula_records = tuple(
        {
            "event_id": r.event_id,
            "datetime": r.dt.isoformat(),
            "value_ml": r.value,
            **({"memo": r.memo} if r.memo is not None else {}),
        }
        for r in reversed(formula_records[-RECENT_FORMULA_LIMIT:])
    )

    daily_summaries = _compute_daily_summaries(
        parsed_records,
        sleep_intervals,
        tz=tz,
        range_from_utc=range_from,
        effective_now_utc=effective_now,
    )
    daily_by_str = {d.day: d for d in daily_summaries}
    yesterday_str = (local_today - timedelta(days=1)).isoformat()
    day_before_str = (local_today - timedelta(days=2)).isoformat()
    yesterday_summary = daily_by_str.get(yesterday_str)
    day_before_yesterday_summary = daily_by_str.get(day_before_str)

    # 7-day averages from complete past days (excluding current in-progress day)
    today_str = local_today.isoformat()
    complete_past_days = [
        d for d in daily_summaries if d.is_complete and d.day != today_str
    ][-7:]
    avg_7d_formula_ml = (
        round(sum(d.formula_ml for d in complete_past_days) / len(complete_past_days), 1)
        if complete_past_days
        else None
    )
    avg_7d_diaper_count = (
        round(
            sum(d.diaper_count for d in complete_past_days) / len(complete_past_days), 1
        )
        if complete_past_days
        else None
    )
    avg_7d_sleep_min = (
        round(sum(d.sleep_min for d in complete_past_days) / len(complete_past_days), 1)
        if complete_past_days
        else None
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
        latest_diaper=latest_diaper,
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
        today_diaper_count=today_diaper_count,
        last_24h_diaper_count=last_24h_diaper_count,
        today_total_milk_ml=today_total_milk_ml,
        sleep=sleep_summary,
        feeding_interval=feeding_interval,
        diaper_interval=diaper_interval,
        pee_interval=pee_interval,
        poop_interval=poop_interval,
        recent_formula_records=recent_formula_records,
        daily_summaries=daily_summaries,
        yesterday_summary=yesterday_summary,
        day_before_yesterday_summary=day_before_yesterday_summary,
        avg_7d_formula_ml=avg_7d_formula_ml,
        avg_7d_diaper_count=avg_7d_diaper_count,
        avg_7d_sleep_min=avg_7d_sleep_min,
        recent_records=recent_records,
    )
