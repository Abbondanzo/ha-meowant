"""DataUpdateCoordinator for Meowant."""
from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MeowantApiClient, MeowantApiError, MeowantAuthError
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN, RECORDS_PER_POLL
from .live import MeowantLive
from .models import BoxState, Cat, Record, parse_cats

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MeowantData:
    boxes: dict[str, BoxState]
    cats: dict[int, Cat]
    cats_ok: bool
    # Records first seen this poll, oldest first, per box. Empty on the first poll.
    new_records: dict[str, list[Record]] = field(default_factory=dict)


class MeowantCoordinator(DataUpdateCoordinator[MeowantData]):
    """Polls every box on the account over REST on a fixed interval.

    Live work state and commands go over MQTT instead, through `live`.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MeowantApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.api = api
        self._seen_records: dict[str, set[int]] = {}
        self.live = MeowantLive(hass, entry, api, lambda: list(self.data.boxes) if self.data else [])

    async def _async_update_data(self) -> MeowantData:
        try:
            devices = await self.api.get_devices()
        except MeowantAuthError as err:
            # Credentials no longer valid -- triggers HA's reauth flow rather than failing silently.
            raise ConfigEntryAuthFailed(str(err)) from err
        except MeowantApiError as err:
            raise UpdateFailed(str(err)) from err

        boxes: dict[str, BoxState] = {}
        for device in devices:
            serial = device.get("serialNumber") if isinstance(device, dict) else None
            if not serial:
                continue
            boxes[serial] = BoxState.parse(
                device,
                config=await self._optional(self.api.get_all_config(serial), "config", serial),
                waste_bin=await self._optional(self.api.get_box_use_times(serial), "waste bin", serial),
                deodorant=await self._optional(self.api.get_deodorant_status(serial), "deodorant", serial),
                online=await self._optional(self.api.get_online_status(serial), "online status", serial),
                cat_visits=await self._optional(self.api.get_records(serial, 0, RECORDS_PER_POLL), "cat visits", serial),
                device_events=await self._optional(self.api.get_records(serial, 1, RECORDS_PER_POLL), "device events", serial),
            )

        cats_raw = await self._optional(self.api.get_cats(), "cats", None)
        return MeowantData(
            boxes=boxes,
            cats=parse_cats(cats_raw),
            cats_ok=cats_raw is not None,
            new_records={serial: self._take_new_records(box) for serial, box in boxes.items()},
        )

    async def _optional(self, call: Awaitable[Any], what: str, serial: str | None) -> Any:
        """Run one per-section call. A failure blanks only that section for this poll."""
        try:
            return await call
        except MeowantAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MeowantApiError as err:
            _LOGGER.debug("Couldn't fetch %s for %s: %s", what, serial or "account", err)
            return None

    def _take_new_records(self, box: BoxState) -> list[Record]:
        """Records not seen in earlier polls, oldest first. The first poll only sets a baseline."""
        if box.cat_visits is None and box.device_events is None:
            return []
        records = box.records
        seen = self._seen_records.get(box.serial)
        self._seen_records[box.serial] = (seen or set()) | {r.record_id for r in records}
        if seen is None:
            return []
        return sorted((r for r in records if r.record_id not in seen), key=lambda r: r.time)
