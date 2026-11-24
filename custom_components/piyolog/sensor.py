"""Sensor platform for PiyoLog Data Feed (schema v1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfLength,
    UnitOfMass,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PiyoLogConfigEntry
from .const import (
    DAILY_HISTORY_ATTRIBUTE_DAYS,
    DOMAIN,
    STATUS_OK,
    STATUS_RATE_LIMITED,
    STATUS_TEMPORARY_ERROR,
    STATUS_TOO_LARGE,
    STATUS_UNAVAILABLE,
    STATUS_UNSUPPORTED_SCHEMA,
    TYPE_BATH,
    TYPE_BREAST_FEEDING,
    TYPE_CHEST,
    TYPE_COUGH,
    TYPE_DRINK,
    TYPE_EXPRESSED_BREAST_MILK,
    TYPE_FORMULA,
    TYPE_HEAD,
    TYPE_HEIGHT,
    TYPE_HOSPITAL,
    TYPE_INJURY,
    TYPE_MEAL,
    TYPE_MEDICINE,
    TYPE_MEMO,
    TYPE_MILESTONE,
    TYPE_OTHER,
    TYPE_PEE,
    TYPE_POOP,
    TYPE_PUMPING,
    TYPE_RASH,
    TYPE_SLEEP,
    TYPE_SNACK,
    TYPE_SOLID,
    TYPE_TEMPERATURE,
    TYPE_VACCINE,
    TYPE_VOMITING,
    TYPE_WAKE_UP,
    TYPE_WALKING,
    TYPE_WEIGHT,
)
from .coordinator import PiyoLogCoordinatorData, PiyoLogDataUpdateCoordinator
from .parser import IntervalStats, ParsedRecord, PiyoLogSnapshot


@dataclass(frozen=True, kw_only=True)
class PiyoLogSensorEntityDescription(SensorEntityDescription):
    """Describes a PiyoLog sensor entity."""

    value_fn: Callable[[PiyoLogCoordinatorData], datetime | float | int | str | None]
    attrs_fn: Callable[[PiyoLogCoordinatorData], dict[str, Any]] | None = None


def _snap(data: PiyoLogCoordinatorData) -> PiyoLogSnapshot | None:
    return data.snapshot


def _latest_dt(data: PiyoLogCoordinatorData, rtype: str) -> datetime | None:
    snap = _snap(data)
    if snap is None:
        return None
    rec = snap.latest_by_type.get(rtype)
    return rec.dt if rec is not None else None


def _basic_event_attrs(
    data: PiyoLogCoordinatorData,
    rtype: str,
    *,
    include_today_count: bool = False,
) -> dict[str, Any]:
    snap = _snap(data)
    if snap is None:
        return {}
    rec = snap.latest_by_type.get(rtype)
    attrs: dict[str, Any] = {}
    if include_today_count:
        attrs["today_count"] = snap.today_counts_by_type.get(rtype, 0)
        attrs["last_24h_count"] = snap.last_24h_counts_by_type.get(rtype, 0)
    if rec is None:
        return attrs
    attrs["event_id"] = rec.event_id
    if rec.value is not None:
        attrs["value"] = rec.value
    if rec.unit is not None:
        attrs["unit"] = rec.unit
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _measurement_attrs(rec: ParsedRecord | None) -> dict[str, Any]:
    if rec is None:
        return {}
    attrs: dict[str, Any] = {
        "event_id": rec.event_id,
        "measured_at": rec.dt.isoformat(),
    }
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _interval_attrs(stats: IntervalStats | None) -> dict[str, Any]:
    if stats is None:
        return {}
    attrs: dict[str, Any] = {
        "avg_interval_min": stats.avg_interval_min,
        "interval_sample_count": stats.sample_count,
    }
    if stats.next_expected_at is not None:
        attrs["next_expected_at"] = stats.next_expected_at.isoformat()
    if stats.last_at is not None:
        attrs["last_at"] = stats.last_at.isoformat()
    return attrs


def _feeding_attrs(data: PiyoLogCoordinatorData) -> dict[str, Any]:
    snap = _snap(data)
    if snap is None or snap.latest_feeding is None:
        return {}
    rec = snap.latest_feeding
    attrs: dict[str, Any] = {
        "event_id": rec.event_id,
        "type": rec.record_type,
        **_interval_attrs(snap.feeding_interval),
    }
    if rec.value is not None:
        attrs["value_ml"] = rec.value
    if rec.last_side is not None:
        attrs["last_side"] = rec.last_side
    if rec.left_time is not None:
        attrs["left_time_sec"] = rec.left_time
    if rec.right_time is not None:
        attrs["right_time_sec"] = rec.right_time
    if rec.total_breastfeeding_time_sec is not None:
        attrs["total_time_sec"] = rec.total_breastfeeding_time_sec
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _diaper_attrs(data: PiyoLogCoordinatorData) -> dict[str, Any]:
    snap = _snap(data)
    if snap is None or snap.latest_diaper is None:
        return {}
    rec = snap.latest_diaper
    attrs: dict[str, Any] = {
        "event_id": rec.event_id,
        "type": rec.record_type,
        "today_count": snap.today_diaper_count,
        "last_24h_count": snap.last_24h_diaper_count,
        **_interval_attrs(snap.diaper_interval),
    }
    if rec.poop_amount is not None:
        attrs["amount"] = rec.poop_amount
    if rec.poop_hardness is not None:
        attrs["hardness"] = rec.poop_hardness
    if rec.poop_color is not None:
        attrs["color"] = rec.poop_color
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _breastfeeding_attrs(data: PiyoLogCoordinatorData) -> dict[str, Any]:
    snap = _snap(data)
    if snap is None:
        return {}
    rec = snap.latest_by_type.get(TYPE_BREAST_FEEDING)
    if rec is None:
        return {}
    attrs: dict[str, Any] = {"event_id": rec.event_id}
    if rec.last_side is not None:
        attrs["last_side"] = rec.last_side
    if rec.left_time is not None:
        attrs["left_time_sec"] = rec.left_time
    if rec.right_time is not None:
        attrs["right_time_sec"] = rec.right_time
    if rec.total_breastfeeding_time_sec is not None:
        attrs["total_time_sec"] = rec.total_breastfeeding_time_sec
    if rec.value is not None:
        attrs["value_ml"] = rec.value
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _poop_attrs(data: PiyoLogCoordinatorData) -> dict[str, Any]:
    snap = _snap(data)
    if snap is None:
        return {}
    rec = snap.latest_by_type.get(TYPE_POOP)
    if rec is None:
        return {}
    attrs: dict[str, Any] = {
        "event_id": rec.event_id,
        **_interval_attrs(snap.poop_interval),
    }
    if rec.poop_amount is not None:
        attrs["amount"] = rec.poop_amount
    if rec.poop_hardness is not None:
        attrs["hardness"] = rec.poop_hardness
    if rec.poop_color is not None:
        attrs["color"] = rec.poop_color
    if rec.memo is not None:
        attrs["memo"] = rec.memo
    return attrs


def _make_simple_timestamp_description(
    key: str,
    record_type: str,
    icon: str,
    *,
    include_today_count: bool = False,
    enabled_default: bool = True,
) -> PiyoLogSensorEntityDescription:
    return PiyoLogSensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon=icon,
        entity_registry_enabled_default=enabled_default,
        value_fn=lambda d, rt=record_type: _latest_dt(d, rt),
        attrs_fn=lambda d, rt=record_type, tc=include_today_count: _basic_event_attrs(
            d, rt, include_today_count=tc
        ),
    )


def _build_sensor_descriptions() -> tuple[PiyoLogSensorEntityDescription, ...]:
    """Build all sensor entity descriptions covering every PiyoLog v1 record type."""
    descriptions: list[PiyoLogSensorEntityDescription] = [
        # --- Feeding & Milk (Latest) ---
        PiyoLogSensorEntityDescription(
            key="last_feeding",
            translation_key="last_feeding",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:baby-bottle-outline",
            value_fn=lambda d: (
                d.snapshot.latest_feeding.dt
                if d.snapshot and d.snapshot.latest_feeding
                else None
            ),
            attrs_fn=_feeding_attrs,
        ),
        PiyoLogSensorEntityDescription(
            key="last_breastfeeding",
            translation_key="last_breastfeeding",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:mother-nurse",
            value_fn=lambda d: _latest_dt(d, TYPE_BREAST_FEEDING),
            attrs_fn=_breastfeeding_attrs,
        ),
        PiyoLogSensorEntityDescription(
            key="last_breastfeeding_side",
            translation_key="last_breastfeeding_side",
            device_class=SensorDeviceClass.ENUM,
            options=["left", "right"],
            icon="mdi:swap-horizontal",
            value_fn=lambda d: (
                d.snapshot.latest_by_type[TYPE_BREAST_FEEDING].last_side
                if d.snapshot and TYPE_BREAST_FEEDING in d.snapshot.latest_by_type
                else None
            ),
            attrs_fn=_breastfeeding_attrs,
        ),
        PiyoLogSensorEntityDescription(
            key="last_formula",
            translation_key="last_formula",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:baby-bottle",
            value_fn=lambda d: _latest_dt(d, TYPE_FORMULA),
            attrs_fn=lambda d: _basic_event_attrs(d, TYPE_FORMULA),
        ),
        PiyoLogSensorEntityDescription(
            key="last_formula_amount",
            translation_key="last_formula_amount",
            device_class=SensorDeviceClass.VOLUME,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:cup-water",
            value_fn=lambda d: (
                d.snapshot.latest_by_type[TYPE_FORMULA].value
                if d.snapshot and TYPE_FORMULA in d.snapshot.latest_by_type
                else None
            ),
            attrs_fn=lambda d: _basic_event_attrs(d, TYPE_FORMULA),
        ),
        _make_simple_timestamp_description(
            "last_expressed_breast_milk",
            TYPE_EXPRESSED_BREAST_MILK,
            "mdi:baby-bottle-outline",
        ),
        _make_simple_timestamp_description(
            "last_pumping",
            TYPE_PUMPING,
            "mdi:water-pump",
        ),
        PiyoLogSensorEntityDescription(
            key="next_feeding_estimate",
            translation_key="next_feeding_estimate",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:clock-alert-outline",
            value_fn=lambda d: (
                d.snapshot.feeding_interval.next_expected_at if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                _interval_attrs(d.snapshot.feeding_interval) if d.snapshot else {}
            ),
        ),
        # --- Feeding & Milk (Today Totals) ---
        PiyoLogSensorEntityDescription(
            key="today_formula_total",
            translation_key="today_formula_total",
            device_class=SensorDeviceClass.VOLUME,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:baby-bottle",
            value_fn=lambda d: d.snapshot.today_formula_ml if d.snapshot else None,
            attrs_fn=lambda d: (
                {
                    "count": d.snapshot.today_counts_by_type.get(TYPE_FORMULA, 0),
                    "avg_per_feed_ml": (
                        round(
                            d.snapshot.today_formula_ml
                            / d.snapshot.today_counts_by_type.get(TYPE_FORMULA, 1),
                            1,
                        )
                        if d.snapshot.today_counts_by_type.get(TYPE_FORMULA, 0) > 0
                        else None
                    ),
                    "last_24h_total_ml": d.snapshot.last_24h_formula_ml,
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_FORMULA, 0
                    ),
                    "yesterday_total_ml": (
                        d.snapshot.yesterday_summary.formula_ml
                        if d.snapshot.yesterday_summary
                        else None
                    ),
                    "yesterday_count": (
                        d.snapshot.yesterday_summary.formula_count
                        if d.snapshot.yesterday_summary
                        else None
                    ),
                    "avg_7d_total_ml": d.snapshot.avg_7d_formula_ml,
                    "recent_records": list(d.snapshot.recent_formula_records),
                    "daily_history": [
                        {
                            "date": day.day,
                            "formula_ml": day.formula_ml,
                            "count": day.formula_count,
                            "avg_ml": day.formula_avg_ml,
                            "is_complete": day.is_complete,
                        }
                        for day in d.snapshot.daily_summaries[
                            -DAILY_HISTORY_ATTRIBUTE_DAYS:
                        ]
                    ],
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_breastfeeding_count",
            translation_key="today_breastfeeding_count",
            state_class=SensorStateClass.TOTAL,
            icon="mdi:mother-nurse",
            value_fn=lambda d: (
                d.snapshot.today_counts_by_type.get(TYPE_BREAST_FEEDING, 0)
                if d.snapshot
                else None
            ),
            attrs_fn=lambda d: (
                {
                    "total_left_sec": d.snapshot.today_breastfeeding_left_sec,
                    "total_right_sec": d.snapshot.today_breastfeeding_right_sec,
                    "total_time_sec": (
                        d.snapshot.today_breastfeeding_left_sec
                        + d.snapshot.today_breastfeeding_right_sec
                    ),
                    "total_ml": d.snapshot.today_breastfeeding_ml,
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_BREAST_FEEDING, 0
                    ),
                    "last_24h_left_sec": d.snapshot.last_24h_breastfeeding_left_sec,
                    "last_24h_right_sec": d.snapshot.last_24h_breastfeeding_right_sec,
                    "last_24h_ml": d.snapshot.last_24h_breastfeeding_ml,
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_expressed_milk_total",
            translation_key="today_expressed_milk_total",
            device_class=SensorDeviceClass.VOLUME,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:baby-bottle-outline",
            value_fn=lambda d: (
                d.snapshot.today_expressed_milk_ml if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                {
                    "count": d.snapshot.today_counts_by_type.get(
                        TYPE_EXPRESSED_BREAST_MILK, 0
                    ),
                    "last_24h_total_ml": d.snapshot.last_24h_expressed_milk_ml,
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_EXPRESSED_BREAST_MILK, 0
                    ),
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_pumping_total",
            translation_key="today_pumping_total",
            device_class=SensorDeviceClass.VOLUME,
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:water-pump",
            value_fn=lambda d: d.snapshot.today_pumping_ml if d.snapshot else None,
            attrs_fn=lambda d: (
                {
                    "count": d.snapshot.today_counts_by_type.get(TYPE_PUMPING, 0),
                    "last_24h_total_ml": d.snapshot.last_24h_pumping_ml,
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_PUMPING, 0
                    ),
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_feeding_count",
            translation_key="today_feeding_count",
            state_class=SensorStateClass.TOTAL,
            icon="mdi:counter",
            value_fn=lambda d: d.snapshot.today_feeding_count if d.snapshot else None,
            attrs_fn=lambda d: (
                {
                    "total_milk_ml": d.snapshot.today_total_milk_ml,
                    "last_24h_count": d.snapshot.last_24h_feeding_count,
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        # --- Sleep & Excretion ---
        _make_simple_timestamp_description("last_sleep", TYPE_SLEEP, "mdi:sleep"),
        PiyoLogSensorEntityDescription(
            key="last_wakeup",
            translation_key="last_wakeup",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:weather-sunny",
            value_fn=lambda d: _latest_dt(d, TYPE_WAKE_UP),
            attrs_fn=lambda d: (
                {
                    **_basic_event_attrs(d, TYPE_WAKE_UP),
                    "last_sleep_duration_min": d.snapshot.sleep.last_sleep_duration_min,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_sleep_duration",
            translation_key="today_sleep_duration",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:clock-time-eight-outline",
            value_fn=lambda d: d.snapshot.sleep.today_sleep_min if d.snapshot else None,
            attrs_fn=lambda d: (
                {
                    "sessions_count": d.snapshot.sleep.today_sessions_count,
                    "day_sleep_min": d.snapshot.sleep.today_day_sleep_min,
                    "night_sleep_min": d.snapshot.sleep.today_night_sleep_min,
                    "yesterday_sleep_min": d.snapshot.sleep.yesterday_sleep_min,
                    "yesterday_day_sleep_min": d.snapshot.sleep.yesterday_day_sleep_min,
                    "yesterday_night_sleep_min": d.snapshot.sleep.yesterday_night_sleep_min,
                    "yesterday_complete": d.snapshot.sleep.yesterday_complete,
                    "avg_7d_sleep_min": d.snapshot.avg_7d_sleep_min,
                    "daily_history": [
                        {
                            "date": day.day,
                            "sleep_min": day.sleep_min,
                            "day_sleep_min": day.day_sleep_min,
                            "night_sleep_min": day.night_sleep_min,
                            "is_complete": day.is_complete,
                        }
                        for day in d.snapshot.daily_summaries[
                            -DAILY_HISTORY_ATTRIBUTE_DAYS:
                        ]
                    ],
                    "last_sleep_duration_min": d.snapshot.sleep.last_sleep_duration_min,
                    "last_24h_sleep_min": d.snapshot.sleep.last_24h_sleep_min,
                    "is_partial_boundary": d.snapshot.sleep.is_partial_boundary,
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_day_sleep_duration",
            translation_key="today_day_sleep_duration",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:weather-sunny",
            value_fn=lambda d: (
                d.snapshot.sleep.today_day_sleep_min if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                {
                    "yesterday_day_sleep_min": d.snapshot.sleep.yesterday_day_sleep_min,
                    "yesterday_complete": d.snapshot.sleep.yesterday_complete,
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_night_sleep_duration",
            translation_key="today_night_sleep_duration",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            state_class=SensorStateClass.TOTAL,
            icon="mdi:weather-night",
            value_fn=lambda d: (
                d.snapshot.sleep.today_night_sleep_min if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                {
                    "yesterday_night_sleep_min": d.snapshot.sleep.yesterday_night_sleep_min,
                    "yesterday_complete": d.snapshot.sleep.yesterday_complete,
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="last_diaper",
            translation_key="last_diaper",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:diaper-outline",
            value_fn=lambda d: (
                d.snapshot.latest_diaper.dt
                if d.snapshot and d.snapshot.latest_diaper
                else None
            ),
            attrs_fn=_diaper_attrs,
        ),
        PiyoLogSensorEntityDescription(
            key="next_diaper_estimate",
            translation_key="next_diaper_estimate",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:clock-outline",
            value_fn=lambda d: (
                d.snapshot.diaper_interval.next_expected_at if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                _interval_attrs(d.snapshot.diaper_interval) if d.snapshot else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="last_pee",
            translation_key="last_pee",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:water",
            value_fn=lambda d: _latest_dt(d, TYPE_PEE),
            attrs_fn=lambda d: (
                {
                    **_basic_event_attrs(d, TYPE_PEE),
                    **_interval_attrs(d.snapshot.pee_interval),
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="next_pee_estimate",
            translation_key="next_pee_estimate",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:clock-outline",
            value_fn=lambda d: (
                d.snapshot.pee_interval.next_expected_at if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                _interval_attrs(d.snapshot.pee_interval) if d.snapshot else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_pee_count",
            translation_key="today_pee_count",
            state_class=SensorStateClass.TOTAL,
            icon="mdi:water",
            value_fn=lambda d: (
                d.snapshot.today_counts_by_type.get(TYPE_PEE, 0)
                if d.snapshot
                else None
            ),
            attrs_fn=lambda d: (
                {
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_PEE, 0
                    ),
                    "yesterday_count": (
                        d.snapshot.yesterday_summary.pee_count
                        if d.snapshot.yesterday_summary
                        else None
                    ),
                    "day_before_yesterday_count": (
                        d.snapshot.day_before_yesterday_summary.pee_count
                        if d.snapshot.day_before_yesterday_summary
                        else None
                    ),
                    "yesterday_complete": (
                        d.snapshot.yesterday_summary.is_complete
                        if d.snapshot.yesterday_summary
                        else False
                    ),
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="last_poop",
            translation_key="last_poop",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:emoticon-poop",
            value_fn=lambda d: _latest_dt(d, TYPE_POOP),
            attrs_fn=_poop_attrs,
        ),
        PiyoLogSensorEntityDescription(
            key="next_poop_estimate",
            translation_key="next_poop_estimate",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:clock-outline",
            value_fn=lambda d: (
                d.snapshot.poop_interval.next_expected_at if d.snapshot else None
            ),
            attrs_fn=lambda d: (
                _interval_attrs(d.snapshot.poop_interval) if d.snapshot else {}
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="today_poop_count",
            translation_key="today_poop_count",
            state_class=SensorStateClass.TOTAL,
            icon="mdi:emoticon-poop",
            value_fn=lambda d: (
                d.snapshot.today_counts_by_type.get(TYPE_POOP, 0)
                if d.snapshot
                else None
            ),
            attrs_fn=lambda d: (
                {
                    "last_24h_count": d.snapshot.last_24h_counts_by_type.get(
                        TYPE_POOP, 0
                    ),
                    "yesterday_count": (
                        d.snapshot.yesterday_summary.poop_count
                        if d.snapshot.yesterday_summary
                        else None
                    ),
                    "day_before_yesterday_count": (
                        d.snapshot.day_before_yesterday_summary.poop_count
                        if d.snapshot.day_before_yesterday_summary
                        else None
                    ),
                    "yesterday_complete": (
                        d.snapshot.yesterday_summary.is_complete
                        if d.snapshot.yesterday_summary
                        else False
                    ),
                    "range_covers_today": d.snapshot.range_covers_today,
                }
                if d.snapshot
                else {}
            ),
        ),
        # --- Body Measurements ---
        PiyoLogSensorEntityDescription(
            key="latest_temperature",
            translation_key="latest_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:thermometer",
            value_fn=lambda d: (
                d.snapshot.latest_measurement_by_type[TYPE_TEMPERATURE].value
                if d.snapshot
                and TYPE_TEMPERATURE in d.snapshot.latest_measurement_by_type
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_measurement_by_type.get(TYPE_TEMPERATURE)
                if d.snapshot
                else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_weight",
            translation_key="latest_weight",
            device_class=SensorDeviceClass.WEIGHT,
            native_unit_of_measurement=UnitOfMass.KILOGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:scale-bathroom",
            value_fn=lambda d: (
                d.snapshot.latest_measurement_by_type[TYPE_WEIGHT].value
                if d.snapshot and TYPE_WEIGHT in d.snapshot.latest_measurement_by_type
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_measurement_by_type.get(TYPE_WEIGHT)
                if d.snapshot
                else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_height",
            translation_key="latest_height",
            device_class=SensorDeviceClass.DISTANCE,
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:human-male-height",
            value_fn=lambda d: (
                d.snapshot.latest_measurement_by_type[TYPE_HEIGHT].value
                if d.snapshot and TYPE_HEIGHT in d.snapshot.latest_measurement_by_type
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_measurement_by_type.get(TYPE_HEIGHT)
                if d.snapshot
                else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_head_circumference",
            translation_key="latest_head_circumference",
            device_class=SensorDeviceClass.DISTANCE,
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:head-outline",
            value_fn=lambda d: (
                d.snapshot.latest_measurement_by_type[TYPE_HEAD].value
                if d.snapshot and TYPE_HEAD in d.snapshot.latest_measurement_by_type
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_measurement_by_type.get(TYPE_HEAD)
                if d.snapshot
                else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_chest_circumference",
            translation_key="latest_chest_circumference",
            device_class=SensorDeviceClass.DISTANCE,
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:tape-measure",
            value_fn=lambda d: (
                d.snapshot.latest_measurement_by_type[TYPE_CHEST].value
                if d.snapshot and TYPE_CHEST in d.snapshot.latest_measurement_by_type
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_measurement_by_type.get(TYPE_CHEST)
                if d.snapshot
                else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_foot_size_left",
            translation_key="latest_foot_size_left",
            device_class=SensorDeviceClass.DISTANCE,
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:foot-print",
            value_fn=lambda d: (
                d.snapshot.latest_foot_left.foot_left
                if d.snapshot and d.snapshot.latest_foot_left
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_foot_left if d.snapshot else None
            ),
        ),
        PiyoLogSensorEntityDescription(
            key="latest_foot_size_right",
            translation_key="latest_foot_size_right",
            device_class=SensorDeviceClass.DISTANCE,
            native_unit_of_measurement=UnitOfLength.CENTIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:foot-print",
            value_fn=lambda d: (
                d.snapshot.latest_foot_right.foot_right
                if d.snapshot and d.snapshot.latest_foot_right
                else None
            ),
            attrs_fn=lambda d: _measurement_attrs(
                d.snapshot.latest_foot_right if d.snapshot else None
            ),
        ),
        # --- Meals & Drinks ---
        _make_simple_timestamp_description(
            "last_solid", TYPE_SOLID, "mdi:spoon-sugar", include_today_count=True
        ),
        _make_simple_timestamp_description(
            "last_meal", TYPE_MEAL, "mdi:silverware-fork-knife", include_today_count=True
        ),
        _make_simple_timestamp_description(
            "last_snack", TYPE_SNACK, "mdi:cookie", include_today_count=True
        ),
        PiyoLogSensorEntityDescription(
            key="last_drink",
            translation_key="last_drink",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:cup",
            value_fn=lambda d: _latest_dt(d, TYPE_DRINK),
            attrs_fn=lambda d: (
                {
                    **_basic_event_attrs(d, TYPE_DRINK, include_today_count=True),
                    "today_total_ml": d.snapshot.today_drink_ml,
                    "last_24h_total_ml": d.snapshot.last_24h_drink_ml,
                }
                if d.snapshot
                else {}
            ),
        ),
        # --- Life, Health & Memo ---
        _make_simple_timestamp_description("last_bath", TYPE_BATH, "mdi:bathtub-outline"),
        _make_simple_timestamp_description("last_walking", TYPE_WALKING, "mdi:baby-carriage"),
        _make_simple_timestamp_description(
            "last_medicine", TYPE_MEDICINE, "mdi:pill", include_today_count=True
        ),
        _make_simple_timestamp_description(
            "last_cough", TYPE_COUGH, "mdi:emoticon-sick-outline", include_today_count=True
        ),
        _make_simple_timestamp_description(
            "last_vomiting", TYPE_VOMITING, "mdi:alert-circle-outline", include_today_count=True
        ),
        _make_simple_timestamp_description("last_rash", TYPE_RASH, "mdi:dots-hexagon"),
        _make_simple_timestamp_description("last_injury", TYPE_INJURY, "mdi:bandage"),
        _make_simple_timestamp_description("last_hospital", TYPE_HOSPITAL, "mdi:hospital-building"),
        _make_simple_timestamp_description("last_vaccine", TYPE_VACCINE, "mdi:needle"),
        _make_simple_timestamp_description("last_milestone", TYPE_MILESTONE, "mdi:star-outline"),
        _make_simple_timestamp_description("last_other", TYPE_OTHER, "mdi:note-outline"),
        _make_simple_timestamp_description("last_memo", TYPE_MEMO, "mdi:note-text-outline"),
        # --- Latest Record (All Types) ---
        PiyoLogSensorEntityDescription(
            key="latest_record",
            translation_key="latest_record",
            device_class=SensorDeviceClass.TIMESTAMP,
            icon="mdi:history",
            value_fn=lambda d: (
                d.snapshot.latest_record.dt
                if d.snapshot and d.snapshot.latest_record
                else None
            ),
            attrs_fn=lambda d: (
                {
                    **(
                        d.snapshot.latest_record.to_dict()
                        if d.snapshot.latest_record
                        else {}
                    ),
                    "recent_records": list(d.snapshot.recent_records),
                }
                if d.snapshot
                else {}
            ),
        ),
        # --- Diagnostics ---
        PiyoLogSensorEntityDescription(
            key="feed_status",
            translation_key="feed_status",
            device_class=SensorDeviceClass.ENUM,
            options=[
                STATUS_OK,
                STATUS_RATE_LIMITED,
                STATUS_TEMPORARY_ERROR,
                STATUS_UNAVAILABLE,
                STATUS_TOO_LARGE,
                STATUS_UNSUPPORTED_SCHEMA,
            ],
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:cloud-check-outline",
            value_fn=lambda d: d.status,
            attrs_fn=lambda d: {
                "period": d.period,
                "retention_days": d.retention_days,
                "history_since": (
                    d.history_since.isoformat() if d.history_since else None
                ),
                "feed_record_count": d.feed_record_count,
                "schema_version": d.snapshot.schema_version if d.snapshot else None,
                "generated_at": (
                    d.snapshot.generated_at.isoformat() if d.snapshot else None
                ),
                "range_from": (
                    d.snapshot.range_from.isoformat() if d.snapshot else None
                ),
                "range_to": d.snapshot.range_to.isoformat() if d.snapshot else None,
                "range_covers_today": (
                    d.snapshot.range_covers_today if d.snapshot else None
                ),
                "last_success": d.last_success.isoformat() if d.last_success else None,
                "last_error_code": d.last_error_code,
                "consecutive_errors": d.consecutive_errors,
                "current_interval_sec": d.current_interval_sec,
                "unknown_types": list(d.snapshot.unknown_types) if d.snapshot else [],
            },
        ),
        PiyoLogSensorEntityDescription(
            key="record_count",
            translation_key="record_count",
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:database-outline",
            value_fn=lambda d: len(d.snapshot.records) if d.snapshot else None,
            attrs_fn=lambda d: (
                {
                    "feed_record_count": d.feed_record_count,
                    "stored_record_count": len(d.snapshot.records),
                    "counts_by_type": d.snapshot.counts_by_type,
                }
                if d.snapshot
                else {}
            ),
        ),
    ]

    # --- Custom1 .. Custom10 (disabled by default, opt-in via entity registry) ---
    for i in range(1, 11):
        descriptions.append(
            _make_simple_timestamp_description(
                f"last_custom{i}",
                f"Custom{i}",
                "mdi:tag-outline",
                include_today_count=True,
                enabled_default=False,
            )
        )

    return tuple(descriptions)


SENSOR_DESCRIPTIONS: tuple[PiyoLogSensorEntityDescription, ...] = (
    _build_sensor_descriptions()
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PiyoLogConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PiyoLog sensor entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        PiyoLogSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class PiyoLogSensor(CoordinatorEntity[PiyoLogDataUpdateCoordinator], SensorEntity):
    """Representation of a PiyoLog Data Feed sensor."""

    entity_description: PiyoLogSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PiyoLogDataUpdateCoordinator,
        entry: PiyoLogConfigEntry,
        description: PiyoLogSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="PiyoLog",
            model=f"Data Feed ({coordinator.period})",
            configuration_url="https://www.piyolog.com/app/piyolog/data_feed/ja/",
        )

    @property
    def native_value(self) -> datetime | float | int | str | None:
        """Return the sensor value."""
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        data = self.coordinator.data
        if data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(data)
