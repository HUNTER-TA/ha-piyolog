"""Unit tests for PiyoLog Data Feed API client and URL utilities."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from custom_components.piyolog.api import (
    PiyoLogApiClient,
    PiyoLogFeedUnavailableError,
    PiyoLogInvalidUrlError,
    PiyoLogRateLimitError,
    PiyoLogResponseTooLargeError,
    PiyoLogTemporaryError,
    PiyoLogUnsupportedSchemaError,
    parse_feed_url,
    redact_feed_url,
)

SAMPLE_SECRET = "super-secret-token-xyz"
SAMPLE_URL = f"https://feed.piyolog.com/v1/feed/24h/feed-abc123/{SAMPLE_SECRET}"


class _FakeResponse:
    def __init__(self, status: int, body: Any = None, json_error: bool = False) -> None:
        self.status = status
        self._body = body
        self._json_error = json_error

    async def json(self, content_type: str | None = None) -> Any:
        if self._json_error:
            raise ValueError("Invalid JSON")
        return self._body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.last_headers: dict[str, str] | None = None
        self.last_url: str | None = None

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        self.last_url = url
        self.last_headers = headers
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _valid_payload(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-09-09T03:00:00.000Z",
        "range": {
            "from": "2026-09-08T03:00:00.000Z",
            "to": "2026-09-09T03:00:00.000Z",
        },
        "records": records if records is not None else [],
    }


@pytest.mark.parametrize("period", ["24h", "3d", "7d", "28d"])
def test_parse_feed_url_valid_periods(period: str) -> None:
    url = f"https://feed.piyolog.com/v1/feed/{period}/my-feed-01/secret-value"
    parsed = parse_feed_url(url)
    assert parsed.period == period
    assert parsed.feed_id == "my-feed-01"
    assert parsed.url == url
    assert "secret-value" not in parsed.redacted_url
    assert parsed.redacted_url.endswith(f"/v1/feed/{period}/my-feed-01/***")


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "http://feed.piyolog.com/v1/feed/24h/fid/sec",
        "https://example.com/v1/feed/24h/fid/sec",
        "https://feed.piyolog.com/v1/feed/24h/fid",
        "https://feed.piyolog.com/v2/feed/24h/fid/sec",
        "https://feed.piyolog.com/v1/feed/24h/fid/sec?foo=bar",
        "https://feed.piyolog.com/v1/feed/24h/fid/sec#frag",
    ],
)
def test_parse_feed_url_invalid(bad_url: str) -> None:
    with pytest.raises(PiyoLogInvalidUrlError):
        parse_feed_url(bad_url)


def test_redact_feed_url_hides_secret() -> None:
    redacted = redact_feed_url(SAMPLE_URL)
    assert SAMPLE_SECRET not in redacted
    assert redacted == "https://feed.piyolog.com/v1/feed/24h/feed-abc123/***"


async def test_fetch_feed_success_and_no_cache_control_header() -> None:
    session = _FakeSession(_FakeResponse(200, _valid_payload()))
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    result = await client.async_fetch_feed(SAMPLE_URL)
    assert result["schema_version"] == 1
    assert result["records"] == []
    assert session.last_headers is not None
    assert "Cache-Control" not in session.last_headers
    assert "cache-control" not in {k.lower() for k in session.last_headers}


async def test_fetch_feed_404_unavailable_does_not_leak_secret() -> None:
    session = _FakeSession(
        _FakeResponse(
            404,
            {"error": {"code": "feed_unavailable", "message": "Unavailable"}},
        )
    )
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogFeedUnavailableError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)

    assert exc_info.value.error_code == "feed_unavailable"
    assert exc_info.value.status_code == 404
    assert SAMPLE_SECRET not in str(exc_info.value)


async def test_fetch_feed_413_too_large() -> None:
    session = _FakeSession(
        _FakeResponse(
            413,
            {"error": {"code": "feed_response_too_large", "message": "Too large"}},
        )
    )
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogResponseTooLargeError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)

    assert exc_info.value.error_code == "feed_response_too_large"
    assert exc_info.value.status_code == 413


async def test_fetch_feed_429_rate_limit() -> None:
    session = _FakeSession(
        _FakeResponse(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "Rate limit"}},
        )
    )
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogRateLimitError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)

    assert exc_info.value.error_code == "rate_limit_exceeded"
    assert exc_info.value.status_code == 429


async def test_fetch_feed_500_temporary_error() -> None:
    session = _FakeSession(
        _FakeResponse(
            500,
            {"error": {"code": "internal_error", "message": "Internal error"}},
        )
    )
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogTemporaryError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)

    assert exc_info.value.error_code == "internal_error"
    assert exc_info.value.status_code == 500


async def test_fetch_feed_unsupported_schema_version() -> None:
    bad_payload = _valid_payload()
    bad_payload["schema_version"] = 2
    session = _FakeSession(_FakeResponse(200, bad_payload))
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogUnsupportedSchemaError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)

    assert exc_info.value.error_code == "unsupported_schema"


async def test_fetch_feed_timeout_and_network_error() -> None:
    timeout_client = PiyoLogApiClient(_FakeSession(exc=TimeoutError()))  # type: ignore[arg-type]
    with pytest.raises(PiyoLogTemporaryError) as exc_info:
        await timeout_client.async_fetch_feed(SAMPLE_URL)
    assert exc_info.value.error_code == "timeout"
    assert SAMPLE_SECRET not in str(exc_info.value)

    net_client = PiyoLogApiClient(
        _FakeSession(exc=aiohttp.ClientConnectionError("conn refused"))
    )  # type: ignore[arg-type]
    with pytest.raises(PiyoLogTemporaryError) as exc_info2:
        await net_client.async_fetch_feed(SAMPLE_URL)
    assert exc_info2.value.error_code == "network_error"
    assert SAMPLE_SECRET not in str(exc_info2.value)


async def test_fetch_feed_invalid_json_body() -> None:
    session = _FakeSession(_FakeResponse(200, json_error=True))
    client = PiyoLogApiClient(session)  # type: ignore[arg-type]

    with pytest.raises(PiyoLogTemporaryError) as exc_info:
        await client.async_fetch_feed(SAMPLE_URL)
    assert exc_info.value.error_code == "invalid_payload"
