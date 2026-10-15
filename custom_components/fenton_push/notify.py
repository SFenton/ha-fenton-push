"""Person targets: ``notify.fenton_<name>`` sends to every device where that person is signed in to Fenton Home.

Each matching device is reached through its own ``mobile_app`` action (``notify.mobile_app_<device name>``), so
``mobile_app`` still decides between its websocket channel and our ``push_url`` and handles Live Activity tokens.
``mobile_app`` registers no shared action of its own (discovery gives it no name, so its base action is the generic
``notify.notify``, which another integration may own). One call per device also means a websocket-only device that
isn't connected, which ``mobile_app`` raises for, is logged and the rest still get the notification. The
notification data is passed through untouched; ``data.fenton.devices`` (``phone``, ``tablet``, ``watch``) narrows
the devices.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import ATTR_DATA, ATTR_TARGET, ATTR_TITLE, BaseNotificationService
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import slugify

from .const import DOMAIN, MOBILE_APP, TARGET_EVERYONE
from .targets import DEVICE_KINDS, Device, people, resolve, target_names

try:
    from homeassistant.components.notify.legacy import NOTIFY_SERVICES
except ImportError:  # Moved or renamed in a later Home Assistant; fall back to the action naming rule.
    NOTIFY_SERVICES = None

_LOGGER = logging.getLogger(__name__)


def _device_actions(hass: HomeAssistant) -> dict[str, str] | None:
    """Webhook id to ``mobile_app``'s action for that device, as ``mobile_app`` registered them.

    ``None`` when Home Assistant doesn't expose them; two devices with the same name share one action, which
    reaches only the newer, so the older one has none here.
    """
    if NOTIFY_SERVICES is None or (services := hass.data.get(NOTIFY_SERVICES)) is None:
        return None
    actions: dict[str, str] = {}
    for service in services.get(MOBILE_APP, []):
        for action, webhook_id in (getattr(service, "registered_targets", None) or {}).items():
            actions[webhook_id] = action
    return actions


def _device_action(hass: HomeAssistant, actions: dict[str, str] | None, device: Device) -> str | None:
    if actions is not None:
        action = actions.get(device.webhook_id)
    else:
        action = slugify(f"{MOBILE_APP}_{device.name}")
    return action if action and hass.services.has_service("notify", action) else None


async def async_get_service(hass: HomeAssistant, config: ConfigType,
                            discovery_info: DiscoveryInfoType | None = None) -> FentonNotificationService | None:
    """Set up the person targets (loaded by discovery from ``async_setup_entry``)."""
    if discovery_info is None:
        return None
    return FentonNotificationService(discovery_info["entry_id"])


def _kinds(data: Any) -> list[str] | None:
    fenton = data.get("fenton") if isinstance(data, dict) else None
    wanted = fenton.get("devices") if isinstance(fenton, dict) else None
    if isinstance(wanted, str):
        wanted = [wanted]
    if not isinstance(wanted, list):
        return None
    kinds = [k for k in wanted if k in DEVICE_KINDS]
    return kinds or None


class FentonNotificationService(BaseNotificationService):
    """``notify.fenton`` (everyone) plus one target per person with a Home Assistant user."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    @property
    def targets(self) -> dict[str, str]:
        names = {name: name for name in target_names(people(self.hass))}
        names[TARGET_EVERYONE] = TARGET_EVERYONE
        return names

    def _bundle_id(self) -> str | None:
        runtime = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        return runtime.bundle_id if runtime else None

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        bundle_id = self._bundle_id()
        if bundle_id is None:
            _LOGGER.warning("Fenton Home Push isn't loaded; notification dropped")
            return
        data = kwargs.get(ATTR_DATA)
        kinds = _kinds(data)
        devices: dict[str, Device] = {}
        for target in kwargs.get(ATTR_TARGET) or [TARGET_EVERYONE]:
            for device in resolve(self.hass, bundle_id, target, kinds):
                devices.setdefault(device.webhook_id, device)
        if not devices:
            # Nobody is signed in to Fenton Home for this target yet; groups that also list the official app
            # still reach people, so this isn't an error.
            _LOGGER.debug("No signed-in Fenton Home device for %s", kwargs.get(ATTR_TARGET) or TARGET_EVERYONE)
            return
        service_data: dict[str, Any] = {"message": message}
        if (title := kwargs.get(ATTR_TITLE)) is not None:
            service_data[ATTR_TITLE] = title
        if data is not None:
            service_data[ATTR_DATA] = data
        actions = _device_actions(self.hass)
        for device in devices.values():
            if (action := _device_action(self.hass, actions, device)) is None:
                _LOGGER.warning("Couldn't notify %s: Home Assistant has no notify action for it (does another device "
                                "have the same name?)", device.name)
                continue
            try:
                await self.hass.services.async_call("notify", action, service_data, blocking=True)
            except HomeAssistantError as err:
                _LOGGER.warning("Couldn't notify %s: %s", device.name, err)
