"""Binary sensors for Meowant litter boxes (read-only)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MeowantConfigEntry
from .coordinator import MeowantCoordinator
from .entity import MeowantBoxEntity
from .models import BoxState


@dataclass(frozen=True, kw_only=True)
class BoxBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[BoxState], bool | None]


BOX_BINARY_SENSORS: tuple[BoxBinarySensorDescription, ...] = (
    BoxBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda box: box.online,
    ),
    BoxBinarySensorDescription(
        key="waste_bin_needs_emptying",
        translation_key="waste_bin_needs_emptying",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda box: box.waste_bin.needs_emptying if box.waste_bin else None,
    ),
    BoxBinarySensorDescription(
        key="deodorant_expired",
        translation_key="deodorant_expired",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda box: box.deodorant.expired if box.deodorant else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MeowantConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        MeowantBoxBinarySensor(coordinator, serial, description)
        for serial in coordinator.data.boxes
        for description in BOX_BINARY_SENSORS
    )


class MeowantBoxBinarySensor(MeowantBoxEntity, BinarySensorEntity):
    entity_description: BoxBinarySensorDescription

    def __init__(
        self, coordinator: MeowantCoordinator, serial: str, description: BoxBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        box = self.box
        return self.entity_description.value_fn(box) if box else None
