"""The Meowant integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MeowantApiClient
from .coordinator import MeowantCoordinator

# TODO: add Platform.SENSOR (etc.) once the device data model is known.
PLATFORMS: list[Platform] = []

type MeowantConfigEntry = ConfigEntry[MeowantCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MeowantConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = MeowantApiClient(session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = MeowantCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MeowantConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
