"""Constants for the PiyoLog Data Feed integration."""

from __future__ import annotations

DOMAIN = "piyolog"
VERSION = "1.1.0"

CONF_FEED_URL = "feed_url"
CONF_NAME = "name"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_NAME = "PiyoLog"
SUPPORTED_SCHEMA_VERSION = 1

OFFICIAL_FEED_HOST = "feed.piyolog.com"

# Cache retention (= minimum recommended polling interval) per period in seconds
PERIOD_CACHE_SECONDS: dict[str, int] = {
    "24h": 60,
    "3d": 120,
    "7d": 300,
    "28d": 900,
}

# Default polling interval per period in seconds
PERIOD_DEFAULT_INTERVAL_SECONDS: dict[str, int] = {
    "24h": 180,
    "3d": 300,
    "7d": 600,
    "28d": 900,
}

DEFAULT_MIN_INTERVAL_SECONDS = 60
DEFAULT_FALLBACK_INTERVAL_SECONDS = 300
MAX_SCAN_INTERVAL_SECONDS = 86400
MAX_BACKOFF_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 20

RECENT_RECORDS_LIMIT = 20
RECENT_FORMULA_LIMIT = 5
SERVICE_GET_RECORDS = "get_records"
SERVICE_GET_DAILY_SUMMARY = "get_daily_summary"
SERVICE_DEFAULT_LIMIT = 50
SERVICE_MAX_LIMIT = 500

# Daytime window in local time (06:00-18:00 = day, 18:00-06:00 = night)
DAYTIME_START_HOUR = 6
DAYTIME_END_HOUR = 18

# Clustering thresholds (minutes) to merge simultaneous logs into a single session
# when computing average intervals (e.g., mixed breast+formula or simultaneous pee+poop).
FEEDING_CLUSTER_MINUTES = 15
DIAPER_CLUSTER_MINUTES = 5

# Coordinator feed status values
STATUS_OK = "ok"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_TEMPORARY_ERROR = "temporary_error"
STATUS_UNAVAILABLE = "unavailable"
STATUS_TOO_LARGE = "too_large"
STATUS_UNSUPPORTED_SCHEMA = "unsupported_schema"

# Official record types (schema v1)
TYPE_BREAST_FEEDING = "BreastFeeding"
TYPE_FORMULA = "Formula"
TYPE_EXPRESSED_BREAST_MILK = "ExpressedBreastMilk"
TYPE_PUMPING = "Pumping"
TYPE_SLEEP = "Sleep"
TYPE_WAKE_UP = "WakeUp"
TYPE_PEE = "Pee"
TYPE_POOP = "Poop"
TYPE_TEMPERATURE = "Temperature"
TYPE_HEIGHT = "Height"
TYPE_WEIGHT = "Weight"
TYPE_HEAD = "Head"
TYPE_CHEST = "Chest"
TYPE_FOOT = "Foot"
TYPE_SOLID = "Solid"
TYPE_SNACK = "Snack"
TYPE_MEAL = "Meal"
TYPE_DRINK = "Drink"
TYPE_COUGH = "Cough"
TYPE_VOMITING = "Vomiting"
TYPE_RASH = "Rash"
TYPE_INJURY = "Injury"
TYPE_BATH = "Bath"
TYPE_MEDICINE = "Medicine"
TYPE_HOSPITAL = "Hospital"
TYPE_WALKING = "Walking"
TYPE_VACCINE = "Vaccine"
TYPE_MILESTONE = "Milestone"
TYPE_OTHER = "Other"
TYPE_MEMO = "Memo"

CUSTOM_RECORD_TYPES: tuple[str, ...] = tuple(f"Custom{i}" for i in range(1, 11))

FEEDING_TYPES: tuple[str, ...] = (
    TYPE_BREAST_FEEDING,
    TYPE_FORMULA,
    TYPE_EXPRESSED_BREAST_MILK,
)

DIAPER_TYPES: tuple[str, ...] = (
    TYPE_PEE,
    TYPE_POOP,
)

MEASUREMENT_TYPES: tuple[str, ...] = (
    TYPE_TEMPERATURE,
    TYPE_HEIGHT,
    TYPE_WEIGHT,
    TYPE_HEAD,
    TYPE_CHEST,
)

KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        TYPE_BREAST_FEEDING,
        TYPE_FORMULA,
        TYPE_EXPRESSED_BREAST_MILK,
        TYPE_PUMPING,
        TYPE_SLEEP,
        TYPE_WAKE_UP,
        TYPE_PEE,
        TYPE_POOP,
        TYPE_TEMPERATURE,
        TYPE_HEIGHT,
        TYPE_WEIGHT,
        TYPE_HEAD,
        TYPE_CHEST,
        TYPE_FOOT,
        TYPE_SOLID,
        TYPE_SNACK,
        TYPE_MEAL,
        TYPE_DRINK,
        TYPE_COUGH,
        TYPE_VOMITING,
        TYPE_RASH,
        TYPE_INJURY,
        TYPE_BATH,
        TYPE_MEDICINE,
        TYPE_HOSPITAL,
        TYPE_WALKING,
        TYPE_VACCINE,
        TYPE_MILESTONE,
        TYPE_OTHER,
        TYPE_MEMO,
        *CUSTOM_RECORD_TYPES,
    }
)


def min_interval_for_period(period: str) -> int:
    """Return minimum allowed polling interval (cache TTL) for a feed period."""
    return PERIOD_CACHE_SECONDS.get(period, DEFAULT_MIN_INTERVAL_SECONDS)


def default_interval_for_period(period: str) -> int:
    """Return recommended default polling interval for a feed period."""
    return PERIOD_DEFAULT_INTERVAL_SECONDS.get(
        period, DEFAULT_FALLBACK_INTERVAL_SECONDS
    )
