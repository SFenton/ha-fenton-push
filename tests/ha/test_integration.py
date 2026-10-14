"""Config flow, the push endpoint and person targets inside a real Home Assistant, with Apple patched out."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.fenton_push.apns import APNsError
from custom_components.fenton_push.const import (
    CONF_BUNDLE_ID,
    CONF_CRITICAL_ALERTS,
    CONF_KEY,
    CONF_KEY_ID,
    CONF_PUSH_BASE_URL,
    CONF_TEAM_ID,
    DOMAIN,
)

from .. import samples

CLIENT = "custom_components.fenton_push.apns.APNsClient"
PEM = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
DATA = {CONF_KEY: PEM, CONF_KEY_ID: "ABC123DEFG", CONF_TEAM_ID: "TEAM123456", CONF_BUNDLE_ID: "com.sfenton.home"}
STEPHEN, STEPHANIE = "user-stephen", "user-stephanie"


async def test_flow_checks_the_key_with_apple(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    form = {CONF_KEY: "nope", CONF_KEY_ID: "abc123defg", CONF_TEAM_ID: "team123456", CONF_BUNDLE_ID: "com.sfenton.home"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"] == {"base": "invalid_key"}
    form[CONF_KEY] = PEM
    for reason, status, error in (("InvalidProviderToken", 403, "key_rejected"),
                                  ("TopicDisallowed", 400, "bundle_rejected"),
                                  ("ConnectError", 0, "cannot_connect")):
        with patch(f"{CLIENT}.verify", AsyncMock(side_effect=APNsError(status, reason))):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
        assert result["errors"] == {"base": error}
    # A PEM pasted into the one-line field loses its line breaks; it is rebuilt.
    flattened = " ".join(PEM.split("\n"))
    with patch(f"{CLIENT}.verify", AsyncMock(return_value=None)), \
         patch("custom_components.fenton_push.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {**form, CONF_KEY: flattened})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "com.sfenton.home (key ABC123DEFG)"
    assert result["data"] == {**DATA, CONF_KEY: PEM.strip()}


def mobile_app_entry(hass: HomeAssistant, webhook_id: str, user_id: str, *, model: str = "iPhone19,2",
                     app_id: str = "com.sfenton.home", app_data: dict | None = None) -> MockConfigEntry:
    """A mobile_app registration as Home Assistant stores it (the integration itself isn't loaded)."""
    if app_data is None:
        app_data = {"push_url": "http://127.0.0.1:8123/api/fenton_push/apns", "push_token": f"tok-{webhook_id}",
                    "apns_environment": "sandbox"}
    entry = MockConfigEntry(domain="mobile_app", unique_id=webhook_id, data={
        "webhook_id": webhook_id, "user_id": user_id, "app_id": app_id, "device_name": f"{webhook_id} · Fenton",
        "model": model, "manufacturer": "Apple", "app_version": "0.1.0 (1)", "app_data": app_data,
    })
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def configured(hass: HomeAssistant):
    assert await async_setup_component(hass, "http", {})
    hass.states.async_set("person.stephen_fenton", "home", {"user_id": STEPHEN, "friendly_name": "Stephen Fenton"})
    hass.states.async_set("person.stephanie_hobart", "home",
                          {"user_id": STEPHANIE, "friendly_name": "Stephanie Hobart"})
    hass.states.async_set("person.guest", "home", {"friendly_name": "Guest"})
    mobile_app_entry(hass, "hook-stephen-iphone", STEPHEN)
    mobile_app_entry(hass, "hook-stephen-ipad", STEPHEN, model="iPad16,5")
    mobile_app_entry(hass, "hook-stephanie-iphone", STEPHANIE)
    mobile_app_entry(hass, "hook-stephen-signed-out", STEPHEN, app_data={})
    mobile_app_entry(hass, "hook-companion", STEPHEN, app_id="io.robbie.HomeAssistant")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="com.sfenton.home")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def body(webhook_id: str = "hook-stephen-iphone", token: str | None = None, sample: dict | None = None) -> dict:
    sample = dict(sample or samples.FRONT_DOOR_UNSECURED)
    sample["push_token"] = token if token is not None else f"tok-{webhook_id}"
    sample["registration_info"] = {**samples.REGISTRATION_INFO, "webhook_id": webhook_id}
    return sample


async def test_config_endpoint(hass: HomeAssistant, configured, hass_client) -> None:
    client = await hass_client()
    answer = await (await client.get("/api/fenton_push/config")).json()
    assert answer["available"] is True and answer["bundle_id"] == "com.sfenton.home"
    assert answer["push_url"].endswith("/api/fenton_push/apns") and answer["critical_alerts"] is False
    hass.config_entries.async_update_entry(configured, options={CONF_PUSH_BASE_URL: "http://10.0.0.5:8123/",
                                                                CONF_CRITICAL_ALERTS: True})
    await hass.async_block_till_done()
    answer = await (await client.get("/api/fenton_push/config")).json()
    assert answer["push_url"] == "http://10.0.0.5:8123/api/fenton_push/apns" and answer["critical_alerts"] is True


async def test_push_forwards_to_apple(hass: HomeAssistant, configured, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    with patch(f"{CLIENT}.send", AsyncMock(return_value=None)) as send:
        response = await client.post("/api/fenton_push/apns", json=body())
    assert response.status == HTTPStatus.CREATED
    limits = (await response.json())["rateLimits"]
    assert limits["successful"] == 1 and limits["maximum"] == 1000
    token, request, environment = send.await_args.args
    assert token == "tok-hook-stephen-iphone" and environment == "sandbox"
    assert request.payload["fenton"]["tag"] == "sfenton-front-door-unsecured-42"


async def test_push_refuses_strangers(hass: HomeAssistant, configured, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    with patch(f"{CLIENT}.send", AsyncMock(return_value=None)) as send:
        for refused in (body(token="wrong"), body("hook-nobody"), body("hook-companion"),
                        body("hook-stephen-signed-out", token="tok-hook-stephen-signed-out")):
            assert (await client.post("/api/fenton_push/apns", json=refused)).status == HTTPStatus.FORBIDDEN
        assert (await client.post("/api/fenton_push/apns", data=b"nope")).status == HTTPStatus.BAD_REQUEST
        assert (await client.post("/api/fenton_push/apns", json=[1])).status == HTTPStatus.BAD_REQUEST
    send.assert_not_awaited()


async def test_push_reports_apple_failures(hass: HomeAssistant, configured, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    with patch(f"{CLIENT}.send", AsyncMock(side_effect=APNsError(410, "Unregistered"))):
        assert (await client.post("/api/fenton_push/apns", json=body())).status == HTTPStatus.GONE
    with patch(f"{CLIENT}.send", AsyncMock(side_effect=APNsError(403, "InvalidProviderToken"))):
        assert (await client.post("/api/fenton_push/apns", json=body())).status == HTTPStatus.BAD_GATEWAY
    # A token from the other environment is retried there once.
    with patch(f"{CLIENT}.send", AsyncMock(side_effect=[APNsError(400, "BadDeviceToken"), None])) as send:
        assert (await client.post("/api/fenton_push/apns", json=body())).status == HTTPStatus.CREATED
    assert [call.args[2] for call in send.await_args_list] == ["sandbox", "production"]
    bad = body()
    bad["message"] = "y" * 5000
    assert (await client.post("/api/fenton_push/apns", json=bad)).status == HTTPStatus.BAD_REQUEST


async def test_daily_limit(hass: HomeAssistant, configured, hass_client_no_auth) -> None:
    hass.data[DOMAIN][configured.entry_id].counter.limit = 1
    client = await hass_client_no_auth()
    with patch(f"{CLIENT}.send", AsyncMock(return_value=None)):
        assert (await client.post("/api/fenton_push/apns", json=body())).status == HTTPStatus.CREATED
        limited = await client.post("/api/fenton_push/apns", json=body())
    assert limited.status == HTTPStatus.TOO_MANY_REQUESTS
    assert "rateLimits" in await limited.json()


async def test_person_targets(hass: HomeAssistant, configured) -> None:
    calls = async_mock_service(hass, "notify", "mobile_app")
    for service in ("fenton", "fenton_stephen", "fenton_stephanie", "fenton_everyone"):
        assert hass.services.has_service("notify", service), service
    # A person without a user can't own a device, so has no target.
    assert not hass.services.has_service("notify", "fenton_guest")

    await hass.services.async_call("notify", "fenton_stephen", {"message": "Hi", "title": "T", "data": {"tag": "x"}},
                                   blocking=True)
    assert calls[-1].data == {"message": "Hi", "title": "T", "data": {"tag": "x"},
                              "target": ["hook-stephen-iphone", "hook-stephen-ipad"]}

    await hass.services.async_call("notify", "fenton_stephen",
                                   {"message": "Phones", "data": {"fenton": {"devices": ["phone"]}}}, blocking=True)
    assert calls[-1].data["target"] == ["hook-stephen-iphone"]

    await hass.services.async_call("notify", "fenton", {"message": "All"}, blocking=True)
    assert sorted(calls[-1].data["target"]) == ["hook-stephanie-iphone", "hook-stephen-ipad", "hook-stephen-iphone"]

    count = len(calls)
    await hass.services.async_call("notify", "fenton_stephanie",
                                   {"message": "Tablets", "data": {"fenton": {"devices": "tablet"}}}, blocking=True)
    assert len(calls) == count  # Stephanie has no tablet: nothing sent, no error.


async def test_unload(hass: HomeAssistant, configured, hass_client_no_auth) -> None:
    assert await hass.config_entries.async_unload(configured.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service("notify", "fenton_stephen")
    client = await hass_client_no_auth()
    assert (await client.post("/api/fenton_push/apns", json=body())).status == HTTPStatus.SERVICE_UNAVAILABLE


async def test_options_flow(hass: HomeAssistant, configured) -> None:
    result = await hass.config_entries.options.async_init(configured.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CRITICAL_ALERTS: True, CONF_PUSH_BASE_URL: " http://127.0.0.1:8123 "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert configured.options == {CONF_CRITICAL_ALERTS: True, CONF_PUSH_BASE_URL: "http://127.0.0.1:8123"}
    assert hass.data[DOMAIN][configured.entry_id].critical_alerts is True


async def test_unreadable_saved_key(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, CONF_KEY: "garbage"}, unique_id="com.sfenton.home")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
