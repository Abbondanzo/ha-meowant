"""Base entities for Meowant: one HA device per litter box, one per cat."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MeowantCoordinator
from .models import BoxState, Cat

MANUFACTURER = "Meowant"


class MeowantBoxEntity(CoordinatorEntity[MeowantCoordinator]):
    """An entity belonging to one litter box."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MeowantCoordinator, serial: str, key: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        box = self.box
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=box.name if box else "Litter box",
            manufacturer=MANUFACTURER,
            model="Smart cat litter box",
            serial_number=serial,
            sw_version=box.firmware if box else None,
        )

    @property
    def box(self) -> BoxState | None:
        return self.coordinator.data.boxes.get(self._serial) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self.box is not None


class MeowantLiveEntity(MeowantBoxEntity):
    """A box entity driven by the live (MQTT) connection rather than REST polls."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.coordinator.live.signal, self._handle_live_update)
        )

    @callback
    def _handle_live_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        # Independent of REST poll failures: only the box having been seen matters.
        return self.box is not None and self.coordinator.live.connected


class MeowantCatEntity(CoordinatorEntity[MeowantCoordinator]):
    """An entity belonging to one cat profile (account-wide, not tied to a box)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MeowantCoordinator, cat_id: int, key: str) -> None:
        super().__init__(coordinator)
        self._cat_id = cat_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_cat_{cat_id}_{key}"
        cat = self.cat
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_cat_{cat_id}")},
            name=cat.name if cat else f"Cat {cat_id}",
            manufacturer=MANUFACTURER,
            model=(cat.breed if cat else None) or "Cat",
        )

    @property
    def cat(self) -> Cat | None:
        return self.coordinator.data.cats.get(self._cat_id) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self.cat is not None
