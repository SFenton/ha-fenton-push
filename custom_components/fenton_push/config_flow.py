"""Config flow: paste the APNs auth key (.p8), its Key ID and the Team ID, checked with Apple before saving."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .apns import APNsClient, APNsError
from .const import (
    CONF_BUNDLE_ID,
    CONF_CRITICAL_ALERTS,
    CONF_KEY,
    CONF_KEY_ID,
    CONF_PUSH_BASE_URL,
    CONF_TEAM_ID,
    DEFAULT_BUNDLE_ID,
    DOMAIN,
)

KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, multiline=False))


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    key = user_input[CONF_KEY].strip()
    # Pasted into a single-line field, the PEM's line breaks may arrive as spaces.
    if "\n" not in key and "-----BEGIN PRIVATE KEY-----" in key:
        body = key.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").split()
        key = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END PRIVATE KEY-----"
    return {CONF_KEY: key, CONF_KEY_ID: user_input[CONF_KEY_ID].strip().upper(),
            CONF_TEAM_ID: user_input[CONF_TEAM_ID].strip().upper(),
            CONF_BUNDLE_ID: user_input[CONF_BUNDLE_ID].strip()}


class FentonPushConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Apple Push for the Fenton Home bundle."""

    VERSION = 1

    async def _verify(self, data: dict[str, Any]) -> str | None:
        """Return an error code, or None when Apple accepts the key for this bundle."""
        from . import credentials, http_client  # noqa: PLC0415

        try:
            client = APNsClient(credentials(data), http_client(self.hass))
        except ValueError:
            return "invalid_key"
        try:
            await client.verify()
        except APNsError as err:
            if err.status == 0:
                return "cannot_connect"
            if err.reason in ("TopicDisallowed", "BadTopic", "DeviceTokenNotForTopic"):
                return "bundle_rejected"
            if err.bad_credentials or err.status == 403:
                return "key_rejected"
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the key and identifiers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            await self.async_set_unique_id(data[CONF_BUNDLE_ID])
            self._abort_if_unique_id_configured()
            if (error := await self._verify(data)) is None:
                return self.async_create_entry(title=f"{data[CONF_BUNDLE_ID]} (key {data[CONF_KEY_ID]})", data=data)
            errors["base"] = error
        schema = vol.Schema({
            vol.Required(CONF_KEY): KEY_SELECTOR,
            vol.Required(CONF_KEY_ID): str,
            vol.Required(CONF_TEAM_ID): str,
            vol.Required(CONF_BUNDLE_ID, default=DEFAULT_BUNDLE_ID): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FentonPushOptionsFlow:
        return FentonPushOptionsFlow()


class FentonPushOptionsFlow(OptionsFlow):
    """Critical alerts (once Apple grants the entitlement) and an explicit push base URL."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={
                CONF_CRITICAL_ALERTS: bool(user_input.get(CONF_CRITICAL_ALERTS, False)),
                CONF_PUSH_BASE_URL: (user_input.get(CONF_PUSH_BASE_URL) or "").strip(),
            })
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_CRITICAL_ALERTS, default=options.get(CONF_CRITICAL_ALERTS, False)): BooleanSelector(),
            vol.Optional(CONF_PUSH_BASE_URL, description={"suggested_value": options.get(CONF_PUSH_BASE_URL, "")}):
                TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
