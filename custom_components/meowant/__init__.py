"""The Meowant integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthSession, MeowantApiClient
from .const import CONF_AREA_CODE, CONF_AUTH, CONF_DEVICE_ID
from .coordinator import MeowantCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR]

type MeowantConfigEntry = ConfigEntry[MeowantCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MeowantConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    @callback
    def _save_auth(auth: AuthSession) -> None:
        # Reused on the next setup, so restarts and setup retries don't log in again.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_AUTH: auth.as_dict()})

    api = MeowantApiClient(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        device_id=entry.data[CONF_DEVICE_ID],
        area_code=entry.data[CONF_AREA_CODE],
        auth=AuthSession.from_dict(entry.data.get(CONF_AUTH)),
        on_login=_save_auth,
    )
    coordinator = MeowantCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.live.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MeowantConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
