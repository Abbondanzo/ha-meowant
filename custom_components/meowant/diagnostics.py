"""Diagnostics for Meowant: the latest raw responses, with personal data redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import MeowantConfigEntry
from .const import CONF_AUTH, CONF_DEVICE_ID

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DEVICE_ID,
    CONF_AUTH,  # login token
    "serialNumber",
    "sn",
    "ip",
    "wifiName",
    "userId",
    "adminUserNickName",
    "userNickName",
    "nickname",
    "catNickname",
    "icon",
    "videoPath",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: MeowantConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "boxes": [box.raw for box in data.boxes.values()] if data else [],
            "cat_count": len(data.cats) if data else 0,
            "live": entry.runtime_data.live.diagnostics(),
        },
        TO_REDACT,
    )
