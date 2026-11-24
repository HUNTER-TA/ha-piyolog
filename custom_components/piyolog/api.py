"""HTTP client and URL utilities for the PiyoLog Data Feed API (v1)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import (
    OFFICIAL_FEED_HOST,
    REQUEST_TIMEOUT_SECONDS,
    SUPPORTED_SCHEMA_VERSION,
    VERSION,
)


class PiyoLogError(Exception):
    """Base exception for PiyoLog Data Feed API errors."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class PiyoLogInvalidUrlError(PiyoLogError):
    """Raised when the configured feed URL does not match the expected format."""


class PiyoLogFeedUnavailableError(PiyoLogError):
    """Raised on HTTP 404 / feed_unavailable."""


class PiyoLogResponseTooLargeError(PiyoLogError):
    """Raised on HTTP 413 / feed_response_too_large."""


class PiyoLogRateLimitError(PiyoLogError):
    """Raised on HTTP 429 / rate_limit_exceeded."""


class PiyoLogTemporaryError(PiyoLogError):
    """Raised on 5xx, network failures, timeouts, or malformed responses."""


class PiyoLogUnsupportedSchemaError(PiyoLogError):
    """Raised when schema_version is missing or unsupported."""


@dataclass(frozen=True)
class ParsedFeedUrl:
    """Parsed metadata from a PiyoLog feed URL (secret excluded from logs)."""

    url: str
    period: str
    feed_id: str
    redacted_url: str


def redact_feed_url(url: str) -> str:
    """Return a log-safe version of a feed URL with the secret masked."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "***"

    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) >= 5 and segments[0] == "v1" and segments[1] == "feed":
        masked_path = f"/v1/feed/{segments[2]}/{segments[3]}/***"
    elif segments:
        masked_path = "/" + "/".join([*segments[:-1], "***"])
    else:
        masked_path = "/***"

    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}{masked_path}"
    return masked_path


def parse_feed_url(
    raw_url: str,
    *,
    allow_custom_host: bool = False,
    allow_insecure: bool = False,
) -> ParsedFeedUrl:
    """Validate and parse a PiyoLog Data Feed URL.

    Expected format:
        https://feed.piyolog.com/v1/feed/{period}/{feed_id}/{secret}
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise PiyoLogInvalidUrlError("Feed URL is empty", error_code="invalid_url")

    url = raw_url.strip()
    parts = urlsplit(url)

    allowed_schemes = ("https", "http") if allow_insecure else ("https",)
    if parts.scheme.lower() not in allowed_schemes:
        raise PiyoLogInvalidUrlError(
            "Feed URL must use HTTPS", error_code="invalid_url"
        )

    if not parts.hostname:
        raise PiyoLogInvalidUrlError(
            "Feed URL is missing a hostname", error_code="invalid_url"
        )

    if not allow_custom_host and parts.hostname.lower() != OFFICIAL_FEED_HOST:
        raise PiyoLogInvalidUrlError(
            f"Feed URL host must be {OFFICIAL_FEED_HOST}",
            error_code="invalid_url",
        )

    if parts.query or parts.fragment:
        raise PiyoLogInvalidUrlError(
            "Feed URL must not include query parameters or fragments",
            error_code="invalid_url",
        )

    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) != 5 or segments[0] != "v1" or segments[1] != "feed":
        raise PiyoLogInvalidUrlError(
            "Feed URL path must match /v1/feed/{period}/{feed_id}/{secret}",
            error_code="invalid_url",
        )

    period, feed_id, secret = segments[2], segments[3], segments[4]
    if not period or not feed_id or not secret:
        raise PiyoLogInvalidUrlError(
            "Feed URL is missing period, feed_id, or secret",
            error_code="invalid_url",
        )

    return ParsedFeedUrl(
        url=url,
        period=period,
        feed_id=feed_id,
        redacted_url=redact_feed_url(url),
    )


def _extract_error_code(payload: Any) -> str | None:
    """Extract error.code from a PiyoLog JSON error response."""
    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            code = error_obj.get("code")
            if isinstance(code, str) and code:
                return code
    return None


def _validate_success_payload(payload: Any) -> dict[str, Any]:
    """Validate required top-level fields and schema_version."""
    if not isinstance(payload, dict):
        raise PiyoLogTemporaryError(
            "Feed response is not a JSON object", error_code="invalid_payload"
        )

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SUPPORTED_SCHEMA_VERSION:
        raise PiyoLogUnsupportedSchemaError(
            f"Unsupported schema_version: {schema_version!r}",
            error_code="unsupported_schema",
        )

    if not isinstance(payload.get("generated_at"), str):
        raise PiyoLogTemporaryError(
            "Missing or invalid generated_at", error_code="invalid_payload"
        )

    range_obj = payload.get("range")
    if (
        not isinstance(range_obj, dict)
        or not isinstance(range_obj.get("from"), str)
        or not isinstance(range_obj.get("to"), str)
    ):
        raise PiyoLogTemporaryError(
            "Missing or invalid range object", error_code="invalid_payload"
        )

    if not isinstance(payload.get("records"), list):
        raise PiyoLogTemporaryError(
            "Missing or invalid records array", error_code="invalid_payload"
        )

    return payload


class PiyoLogApiClient:
    """Async HTTP client for PiyoLog Data Feed API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_fetch_feed(self, feed_url: str) -> dict[str, Any]:
        """Fetch and validate a PiyoLog feed JSON snapshot.

        Never sends cache-busting headers (such as Cache-Control: max-age=0)
        and never includes the raw URL secret in raised exception messages.
        """
        redacted = redact_feed_url(feed_url)
        headers = {
            "Accept": "application/json",
            "User-Agent": f"HomeAssistant-PiyoLog/{VERSION}",
        }
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)

        try:
            async with self._session.get(
                feed_url,
                headers=headers,
                timeout=timeout,
            ) as response:
                status = response.status
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None

                if status == 200:
                    return _validate_success_payload(payload)

                error_code = _extract_error_code(payload)

                if status == 404 or error_code == "feed_unavailable":
                    raise PiyoLogFeedUnavailableError(
                        f"Feed unavailable ({redacted})",
                        error_code=error_code or "feed_unavailable",
                        status_code=status,
                    )

                if status == 413 or error_code == "feed_response_too_large":
                    raise PiyoLogResponseTooLargeError(
                        f"Feed response too large ({redacted})",
                        error_code=error_code or "feed_response_too_large",
                        status_code=status,
                    )

                if status == 429 or error_code == "rate_limit_exceeded":
                    raise PiyoLogRateLimitError(
                        f"Rate limit exceeded ({redacted})",
                        error_code=error_code or "rate_limit_exceeded",
                        status_code=status,
                    )

                if status >= 500 or error_code == "internal_error":
                    raise PiyoLogTemporaryError(
                        f"Server error HTTP {status} ({redacted})",
                        error_code=error_code or f"http_{status}",
                        status_code=status,
                    )

                raise PiyoLogTemporaryError(
                    f"Unexpected HTTP {status} ({redacted})",
                    error_code=error_code or f"http_{status}",
                    status_code=status,
                )

        except PiyoLogError:
            raise
        except TimeoutError as err:
            raise PiyoLogTemporaryError(
                f"Request timed out ({redacted})",
                error_code="timeout",
            ) from err
        except aiohttp.ClientError as err:
            raise PiyoLogTemporaryError(
                f"Network error while fetching feed ({redacted}): {type(err).__name__}",
                error_code="network_error",
            ) from err
        except asyncio.CancelledError:
            raise
