"""Person targets: ``notify.fenton_<name>`` sends to every device where that person is signed in to Fenton Home.

Each call is handed to ``notify.mobile_app`` with the matching webhook ids, so ``mobile_app`` still decides
between its websocket channel and our ``push_url`` and handles Live Activity tokens. The notification data
is passed through untouched; ``data.fenton.devices`` (``phone``, ``tablet``, ``watch``) narrows the devices.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import ATTR_DATA, ATTR_TARGET, ATTR_TITLE, BaseNotificationService
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN, MOBILE_APP, TARGET_EVERYONE
from .targets import DEVICE_KINDS, people, resolve, target_names

_LOGGER = logging.getLogger(__name__)


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
        webhook_ids: list[str] = []
        for target in kwargs.get(ATTR_TARGET) or [TARGET_EVERYONE]:
            for device in resolve(self.hass, bundle_id, target, kinds):
                if device.webhook_id not in webhook_ids:
                    webhook_ids.append(device.webhook_id)
        if not webhook_ids:
            # Nobody is signed in to Fenton Home for this target yet; groups that also list the official app
            # still reach people, so this isn't an error.
            _LOGGER.debug("No signed-in Fenton Home device for %s", kwargs.get(ATTR_TARGET) or TARGET_EVERYONE)
            return
        service_data: dict[str, Any] = {"message": message, ATTR_TARGET: webhook_ids}
        if (title := kwargs.get(ATTR_TITLE)) is not None:
            service_data[ATTR_TITLE] = title
        if data is not None:
            service_data[ATTR_DATA] = data
        await self.hass.services.async_call("notify", MOBILE_APP, service_data, blocking=True)
