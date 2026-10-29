"""DataUpdateCoordinator for Meowant."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MeowantApiClient, MeowantApiError, MeowantAuthError, MeowantConnectionError
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    FINISHED_REFRESH_DELAY,
    RECORDS_PER_POLL,
    SLOW_UPDATE_INTERVAL,
)
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
    """Polls every box on the account over REST.

    Sections that change with activity (records, waste bin, online, cats) are
    fetched every poll; the device list, settings and deodorant only every
    SLOW_UPDATE_INTERVAL. Live work state and commands go over MQTT instead,
    through `live`, which also triggers a poll when a clean or level finishes.
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
        self._devices: list[dict[str, Any]] | None = None
        self._slow_fetched_at: float | None = None  # monotonic
        # serial -> {"config": ..., "deodorant": ...}, raw, from the last slow fetch
        self._slow_sections: dict[str, dict[str, Any]] = {}
        self.live = MeowantLive(
            hass, entry, api, lambda: list(self.data.boxes) if self.data else [], on_finished=self._on_finished
        )

    async def _async_update_data(self) -> MeowantData:
        try:
            return await self._update()
        except MeowantAuthError as err:
            # Credentials no longer valid -- triggers HA's reauth flow rather than failing silently.
            raise ConfigEntryAuthFailed(str(err)) from err
        except MeowantApiError as err:
            raise UpdateFailed(str(err)) from err

    async def _update(self) -> MeowantData:
        slow_due = (
            self._slow_fetched_at is None
            or time.monotonic() - self._slow_fetched_at >= SLOW_UPDATE_INTERVAL.total_seconds()
        )
        if slow_due or self._devices is None:
            self._devices = await self.api.get_devices()

        boxes: dict[str, BoxState] = {}
        for device in self._devices:
            serial = device.get("serialNumber") if isinstance(device, dict) else None
            if not serial:
                continue
            slow = self._slow_sections.setdefault(serial, {"config": None, "deodorant": None})
            # Refetch slow sections when due, or sooner if the last attempt failed.
            if slow_due or slow["config"] is None:
                slow["config"] = await self._optional(self.api.get_all_config(serial), "config", serial)
            if slow_due or slow["deodorant"] is None:
                slow["deodorant"] = await self._optional(self.api.get_deodorant_status(serial), "deodorant", serial)
            boxes[serial] = BoxState.parse(
                device,
                config=slow["config"],
                waste_bin=await self._optional(self.api.get_box_use_times(serial), "waste bin", serial),
                deodorant=slow["deodorant"],
                online=await self._optional(self.api.get_online_status(serial), "online status", serial),
                cat_visits=await self._optional(self.api.get_records(serial, 0, RECORDS_PER_POLL), "cat visits", serial),
                device_events=await self._optional(self.api.get_records(serial, 1, RECORDS_PER_POLL), "device events", serial),
            )
        if slow_due:
            self._slow_fetched_at = time.monotonic()

        cats_raw = await self._optional(self.api.get_cats(), "cats", None)
        return MeowantData(
            boxes=boxes,
            cats=parse_cats(cats_raw),
            cats_ok=cats_raw is not None,
            new_records={serial: self._take_new_records(box) for serial, box in boxes.items()},
        )

    async def _optional(self, call: Awaitable[Any], what: str, serial: str | None) -> Any:
        """Run one per-section call. An error reply blanks only that section for this poll.

        A connection failure aborts the whole poll instead: the server is
        unreachable, so the remaining calls would only add more attempts.
        """
        try:
            return await call
        except MeowantConnectionError:
            raise
        except MeowantAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MeowantApiError as err:
            _LOGGER.debug("Couldn't fetch %s for %s: %s", what, serial or "account", err)
            return None

    @callback
    def _on_finished(self, serial: str) -> None:
        """A clean or level just finished: its record (and maybe a cat visit) is new."""
        assert self.config_entry is not None
        self.config_entry.async_create_background_task(
            self.hass, self._refresh_after_finish(), f"{DOMAIN} refresh after {serial} finished"
        )

    async def _refresh_after_finish(self) -> None:
        await asyncio.sleep(FINISHED_REFRESH_DELAY.total_seconds())
        await self.async_request_refresh()

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
