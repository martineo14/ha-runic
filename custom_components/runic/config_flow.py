"""Config flow for Runic.io."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RunicApiClient, RunicAuthError, RunicError
from .const import CONF_API_TOKEN, CONF_SIGNING_SECRET, DOMAIN


class RunicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Step 1: API token. Step 2: register the webhook in the Runic portal."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._webhook_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = RunicApiClient(
                async_get_clientsession(self.hass), user_input[CONF_API_TOKEN]
            )
            try:
                await client.list_vehicles()
            except RunicAuthError:
                errors["base"] = "invalid_auth"
            except RunicError:
                errors["base"] = "cannot_connect"
            else:
                self._token = user_input[CONF_API_TOKEN]
                self._webhook_id = webhook.async_generate_id()
                return await self.async_step_webhook()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    async def async_step_webhook(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
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
                )

        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({vol.Required(CONF_SIGNING_SECRET): str}),
            description_placeholders={
                "webhook_url": webhook.async_generate_url(self.hass, self._webhook_id),
                "portal_url": "https://developers.runic.io/portal/webhooks",
            },
            errors=errors,
        )
