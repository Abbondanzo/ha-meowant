"""DataUpdateCoordinator for Meowant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MeowantApiClient, MeowantApiError, MeowantAuthError
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MeowantCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the account's litter boxes on a fixed interval; data is keyed by serial number."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MeowantApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self._api = api

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self._api.get_devices()
        except MeowantAuthError as err:
            # Credentials no longer valid -- triggers HA's reauth flow rather than failing silently.
            raise ConfigEntryAuthFailed(str(err)) from err
        except MeowantApiError as err:
            raise UpdateFailed(str(err)) from err
        return {d["serialNumber"]: d for d in devices if d.get("serialNumber")}
