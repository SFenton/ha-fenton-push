"""HTTP endpoints.

- ``GET /api/fenton_push/config`` (Home Assistant access token): tells the app which ``push_url`` to register.
- ``POST /api/fenton_push/apns`` (no token): the ``push_url`` itself. ``mobile_app`` posts here without
  credentials, so a request is only honoured when its webhook id and push token match a real Fenton Home
  registration; both are secrets Home Assistant and the app share.

Answers follow what ``mobile_app`` expects from a push server: 201 with ``rateLimits`` when sent, 429 once a
device's daily allowance is spent, otherwise an error status with a ``message`` that Home Assistant logs.
"""

from __future__ import annotations

import hmac
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .apns import ENVIRONMENTS, APNsError
from .const import API_CONFIG, API_PUSH, APP_DATA_ENVIRONMENT, CONF_PUSH_BASE_URL, DAILY_LIMIT, DOMAIN
from .payload import PayloadError, build
from .targets import registration

if TYPE_CHECKING:
    from . import PushRuntime

_LOGGER = logging.getLogger(__name__)
_REGISTERED = f"{DOMAIN}_views"


def register_views(hass: HomeAssistant) -> None:
    """Register the endpoints once per Home Assistant run."""
    if hass.data.get(_REGISTERED):
        return
    hass.http.register_view(ConfigView())
    hass.http.register_view(PushView())
    hass.data[_REGISTERED] = True


def _runtime(hass: HomeAssistant) -> PushRuntime | None:
    """The loaded entry's runtime (one bundle id is expected)."""
    return next(iter((hass.data.get(DOMAIN) or {}).values()), None)


def push_url(hass: HomeAssistant, runtime: PushRuntime) -> str:
    """Where ``mobile_app`` should post: the configured base, else Home Assistant's internal URL."""
    base = (runtime.options.get(CONF_PUSH_BASE_URL) or "").strip().rstrip("/")
    if not base:
        try:
            base = get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)
        except NoURLAvailableError:
            base = f"http://127.0.0.1:{hass.http.server_port or 8123}"
    return f"{base}{API_PUSH}"


class DailyCounter:
    """Pushes per device per UTC day, reset at midnight like Home Assistant's own relay."""

    def __init__(self, limit: int = DAILY_LIMIT) -> None:
        self.limit = limit
        self._day = datetime.now(UTC).date()
        self.sent: dict[str, int] = defaultdict(int)
        self.errors: dict[str, int] = defaultdict(int)

    def _roll(self, now: datetime) -> None:
        if now.date() != self._day:
            self._day = now.date()
            self.sent.clear()
            self.errors.clear()

    def exhausted(self, key: str, now: datetime | None = None) -> bool:
        self._roll(now or datetime.now(UTC))
        return self.sent[key] >= self.limit

    def rate_limits(self, key: str) -> dict[str, Any]:
        resets = datetime.combine(self._day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        return {"successful": self.sent[key], "errors": self.errors[key], "maximum": self.limit,
                "resetsAt": resets.isoformat()}


def _message(text: str, status: HTTPStatus) -> web.Response:
    return web.json_response({"message": text}, status=status)


class ConfigView(HomeAssistantView):
    """What the app needs before it registers."""

    url = API_CONFIG
    name = "api:fenton_push:config"

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        runtime = _runtime(hass)
        if runtime is None:
            return self.json({"available": False, "push_url": None, "bundle_id": None, "critical_alerts": False})
        return self.json({"available": True, "push_url": push_url(hass, runtime), "bundle_id": runtime.bundle_id,
                          "critical_alerts": runtime.critical_alerts})


class PushView(HomeAssistantView):
    """Receives ``mobile_app``'s push posts and forwards them to Apple."""

    url = API_PUSH
    name = "api:fenton_push:apns"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        runtime = _runtime(hass)
        if runtime is None:
            return _message("Fenton Home Push isn't set up", HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            body: Any = await request.json()
        except ValueError:
            return _message("Body isn't JSON", HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict):
            return _message("Body isn't a JSON object", HTTPStatus.BAD_REQUEST)

        info = body.get("registration_info") if isinstance(body.get("registration_info"), dict) else {}
        webhook_id = str(info.get("webhook_id") or "")
        entry = registration(hass, webhook_id) if webhook_id else None
        app_data = (entry or {}).get("app_data") or {}
        token = str(body.get("push_token") or "")
        if (entry is None or entry.get("app_id") != runtime.bundle_id or not token
                or not hmac.compare_digest(token, str(app_data.get("push_token") or ""))):
            return _message("Unknown Fenton Home registration", HTTPStatus.FORBIDDEN)

        counter = runtime.counter
        if counter.exhausted(webhook_id):
            return web.json_response({"message": f"Daily limit of {counter.limit} reached for this device",
                                      "rateLimits": counter.rate_limits(webhook_id)},
                                     status=HTTPStatus.TOO_MANY_REQUESTS)
        try:
            apns_request = build(body, critical_enabled=runtime.critical_alerts)
        except PayloadError as err:
            counter.errors[webhook_id] += 1
            return _message(str(err), HTTPStatus.BAD_REQUEST)
        for note in apns_request.notes:
            _LOGGER.debug("%s: %s", entry.get("device_name"), note)

        device_token = str(body.get(apns_request.token_key) or "")
        environment = app_data.get(APP_DATA_ENVIRONMENT)
        environment = environment if environment in ENVIRONMENTS else "production"
        try:
            await runtime.client.send(device_token, apns_request, environment)
        except APNsError as err:
            if err.reason == "BadDeviceToken":
                # A token from the other APNs environment (an Xcode build reporting production, or the reverse).
                other = "sandbox" if environment == "production" else "production"
                try:
                    await runtime.client.send(device_token, apns_request, other)
                except APNsError as retry_err:
                    err = retry_err
                else:
                    counter.sent[webhook_id] += 1
                    return self.json({"rateLimits": counter.rate_limits(webhook_id)}, status_code=HTTPStatus.CREATED)
            counter.errors[webhook_id] += 1
            return self._apns_failure(entry, err)
        counter.sent[webhook_id] += 1
        return self.json({"rateLimits": counter.rate_limits(webhook_id)}, status_code=HTTPStatus.CREATED)

    @staticmethod
    def _apns_failure(entry: dict[str, Any], err: APNsError) -> web.Response:
        name = entry.get("device_name", "device")
        if err.dead_token:
            return _message(f"Apple no longer accepts {name}'s push token ({err.reason}); open Fenton Home on it "
                            "to refresh it", HTTPStatus.GONE)
        if err.bad_credentials:
            _LOGGER.error("Apple rejected the Fenton Home Push key (%s); reconfigure the integration", err.reason)
            return _message(f"Apple rejected the push key ({err.reason})", HTTPStatus.BAD_GATEWAY)
        if err.status == 0:
            return _message(f"Couldn't reach Apple ({err.reason})", HTTPStatus.BAD_GATEWAY)
        return _message(f"Apple refused the push ({err.status} {err.reason})", HTTPStatus.BAD_GATEWAY)
