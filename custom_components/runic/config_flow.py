"""Config flow for Runic.io."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RunicApiClient, RunicAuthError, RunicError
from .const import (
    CONF_API_TOKEN,
    CONF_SIGNING_SECRET,
    CONF_STREAM,
    CONF_SYNC_ZONES,
    CONF_ZONE_ALERT_EMAIL,
    DOMAIN,
)
from .stream import uses_stream

PORTAL_URL = "https://developers.runic.io/portal/webhooks"


class RunicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Step 1: API token. Step 2: real-time stream (no public URL) or a webhook."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._webhook_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RunicOptionsFlow()

    async def _check_token(self, token: str) -> str | None:
        try:
            await RunicApiClient(async_get_clientsession(self.hass), token).list_vehicles()
        except RunicAuthError:
            return "invalid_auth"
        except RunicError:
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            if not (error := await self._check_token(token)):
                self._token = token
                return await self.async_step_delivery()
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    async def async_step_delivery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="delivery",
            menu_options=["stream", "webhook"],
            description_placeholders={"portal_url": PORTAL_URL},
        )

    async def async_step_stream(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(
            title="Runic.io", data={CONF_API_TOKEN: self._token}, options={CONF_STREAM: True}
        )

    async def async_step_webhook(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._webhook_id is None:
            self._webhook_id = webhook.async_generate_id()
        if user_input is not None:
            secret = user_input[CONF_SIGNING_SECRET].strip()
            if not secret.startswith("whsec_"):
                errors[CONF_SIGNING_SECRET] = "invalid_secret"
            else:
                return self.async_create_entry(
                    title="Runic.io",
                    data={
                        CONF_API_TOKEN: self._token,
                        CONF_WEBHOOK_ID: self._webhook_id,
                        CONF_SIGNING_SECRET: secret,
                    },
                    options={CONF_STREAM: False},
                )

        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({vol.Required(CONF_SIGNING_SECRET): str}),
            description_placeholders={
                "webhook_url": webhook.async_generate_url(self.hass, self._webhook_id),
                "portal_url": PORTAL_URL,
            },
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            if not (error := await self._check_token(token)):
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_TOKEN: token}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )


class RunicOptionsFlow(OptionsFlow):
    """The real-time stream, and mirroring Home Assistant zones as Runic POIs."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = (user_input.get(CONF_ZONE_ALERT_EMAIL) or "").strip()
            if email and ("@" not in email or " " in email):
                errors[CONF_ZONE_ALERT_EMAIL] = "invalid_email"
            else:
                return self.async_create_entry(data={**user_input, CONF_ZONE_ALERT_EMAIL: email})

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STREAM, default=uses_stream(self.config_entry)): bool,
                    vol.Required(CONF_SYNC_ZONES, default=options.get(CONF_SYNC_ZONES, False)): bool,
                    vol.Optional(
                        CONF_ZONE_ALERT_EMAIL,
                        description={"suggested_value": options.get(CONF_ZONE_ALERT_EMAIL, "")},
                    ): str,
                }
            ),
            errors=errors,
        )
