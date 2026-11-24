"""Config flow, options flow, and reconfigure flow for PiyoLog Data Feed."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    ParsedFeedUrl,
    PiyoLogApiClient,
    PiyoLogFeedUnavailableError,
    PiyoLogInvalidUrlError,
    PiyoLogRateLimitError,
    PiyoLogResponseTooLargeError,
    PiyoLogTemporaryError,
    PiyoLogUnsupportedSchemaError,
    parse_feed_url,
)
from .const import (
    CONF_FEED_URL,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_MIN_INTERVAL_SECONDS,
    DEFAULT_NAME,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    default_interval_for_period,
    min_interval_for_period,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate_feed_url(
    hass,
    raw_url: str,
) -> tuple[ParsedFeedUrl | None, str | None]:
    """Validate URL format and test connectivity against the PiyoLog API."""
    try:
        parsed = parse_feed_url(raw_url)
    except PiyoLogInvalidUrlError:
        return None, "invalid_url"

    session = async_get_clientsession(hass)
    client = PiyoLogApiClient(session)
    try:
        await client.async_fetch_feed(parsed.url)
    except PiyoLogFeedUnavailableError:
        return None, "feed_unavailable"
    except PiyoLogResponseTooLargeError:
        return None, "feed_response_too_large"
    except PiyoLogRateLimitError:
        return None, "rate_limit_exceeded"
    except PiyoLogUnsupportedSchemaError:
        return None, "unsupported_schema"
    except PiyoLogTemporaryError:
        return None, "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating PiyoLog feed %s", parsed.redacted_url)
        return None, "unknown"

    return parsed, None


class PiyoLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PiyoLog Data Feed."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PiyoLogOptionsFlow:
        """Return the options flow handler."""
        return PiyoLogOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user setup step."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            raw_url = user_input[CONF_FEED_URL]
            name = (user_input.get(CONF_NAME) or DEFAULT_NAME).strip() or DEFAULT_NAME
            raw_interval = user_input.get(CONF_SCAN_INTERVAL)

            parsed, error_key = await _async_validate_feed_url(self.hass, raw_url)
            if error_key is not None or parsed is None:
                errors["base"] = error_key or "unknown"
            else:
                min_interval = min_interval_for_period(parsed.period)
                if raw_interval is None or raw_interval == "":
                    interval = default_interval_for_period(parsed.period)
                else:
                    interval = int(raw_interval)
                    if interval < min_interval:
                        errors[CONF_SCAN_INTERVAL] = "interval_too_short"
                        placeholders["min_interval"] = str(min_interval)
                        placeholders["period"] = parsed.period

                if not errors:
                    await self.async_set_unique_id(parsed.feed_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_FEED_URL: parsed.url,
                            CONF_SCAN_INTERVAL: interval,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_FEED_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=DEFAULT_MIN_INTERVAL_SECONDS,
                        max=MAX_SCAN_INTERVAL_SECONDS,
                        step=10,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow replacing the feed URL without losing entities or history."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            raw_url = user_input[CONF_FEED_URL]
            parsed, error_key = await _async_validate_feed_url(self.hass, raw_url)
            if error_key is not None or parsed is None:
                errors["base"] = error_key or "unknown"
            else:
                # Prevent colliding with another configured entry
                if parsed.feed_id != reconfigure_entry.unique_id:
                    await self.async_set_unique_id(parsed.feed_id)
                    self._abort_if_unique_id_configured()

                min_interval = min_interval_for_period(parsed.period)
                current_interval = int(
                    reconfigure_entry.options.get(
                        CONF_SCAN_INTERVAL,
                        reconfigure_entry.data.get(
                            CONF_SCAN_INTERVAL,
                            default_interval_for_period(parsed.period),
                        ),
                    )
                )
                adjusted_interval = max(current_interval, min_interval)

                new_options = dict(reconfigure_entry.options)
                if CONF_SCAN_INTERVAL in new_options:
                    new_options[CONF_SCAN_INTERVAL] = adjusted_interval

                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    unique_id=parsed.feed_id,
                    data_updates={
                        CONF_FEED_URL: parsed.url,
                        CONF_SCAN_INTERVAL: adjusted_interval,
                    },
                    options=new_options,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_FEED_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class PiyoLogOptionsFlow(OptionsFlow):
    """Handle options for PiyoLog Data Feed."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling interval options."""
        errors: dict[str, str] = {}

        try:
            parsed = parse_feed_url(self.config_entry.data[CONF_FEED_URL])
            period = parsed.period
        except PiyoLogInvalidUrlError:
            period = "24h"

        min_interval = min_interval_for_period(period)
        current_interval = int(
            self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(
                    CONF_SCAN_INTERVAL, default_interval_for_period(period)
                ),
            )
        )

        if user_input is not None:
            new_interval = int(user_input[CONF_SCAN_INTERVAL])
            if new_interval < min_interval:
                errors[CONF_SCAN_INTERVAL] = "interval_too_short"
            else:
                return self.async_create_entry(
                    data={CONF_SCAN_INTERVAL: new_interval}
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=max(current_interval, min_interval),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=min_interval,
                        max=MAX_SCAN_INTERVAL_SECONDS,
                        step=10,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "period": period,
                "min_interval": str(min_interval),
            },
        )
