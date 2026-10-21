"""Config flow for Meowant."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    MeowantApiClient,
    MeowantApiError,
    MeowantAuthError,
    MeowantRateLimitError,
    new_device_id,
    normalize_area_code,
)
from .const import CONF_AREA_CODE, CONF_DEVICE_ID, DEFAULT_AREA_CODE, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_AREA_CODE, default=DEFAULT_AREA_CODE): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class MeowantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meowant."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # TODO: switch to a stable account ID from the API once known.
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            try:
                area_code = normalize_area_code(user_input[CONF_AREA_CODE])
            except ValueError:
                errors[CONF_AREA_CODE] = "invalid_area_code"
                return self.async_show_form(
                    step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
                )

            # Stable per config entry, like the app's per-install ID.
            device_id = new_device_id()
            api = MeowantApiClient(
                async_get_clientsession(self.hass),
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                device_id=device_id,
                area_code=area_code,
            )
            try:
                await api.login()
            except MeowantAuthError:
                errors["password"] = "invalid_auth"
            except MeowantRateLimitError:
                errors["base"] = "too_many_attempts"
            except MeowantApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow validation")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data={
                        **user_input,
                        CONF_AREA_CODE: area_code,
                        CONF_DEVICE_ID: device_id,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            api = MeowantApiClient(
                async_get_clientsession(self.hass),
                reauth_entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                device_id=reauth_entry.data[CONF_DEVICE_ID],
                area_code=reauth_entry.data[CONF_AREA_CODE],
            )
            try:
                await api.login()
            except MeowantAuthError:
                errors["password"] = "invalid_auth"
            except MeowantRateLimitError:
                errors["base"] = "too_many_attempts"
            except MeowantApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )
