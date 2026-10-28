"""Buttons that run the litter box, over the live (MQTT) connection."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MeowantConfigEntry
from .coordinator import MeowantCoordinator
from .entity import MeowantLiveEntity
from .im_proto import OP_CLEAN, OP_LEVEL, OP_START
from .live import MeowantLive


@dataclass(frozen=True, kw_only=True)
class BoxButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[MeowantLive, str], Awaitable[None]]


BUTTONS: tuple[BoxButtonDescription, ...] = (
    BoxButtonDescription(
        key="clean",
        translation_key="clean",
        press_fn=lambda live, serial: live.async_operate(serial, OP_CLEAN, OP_START),
    ),
    BoxButtonDescription(
        key="level",
        translation_key="level",
        press_fn=lambda live, serial: live.async_operate(serial, OP_LEVEL, OP_START),
    ),
    # Pause/resume act on whatever the box is doing (cleaning or levelling).
    BoxButtonDescription(
        key="pause",
        translation_key="pause",
        press_fn=lambda live, serial: live.async_pause(serial),
    ),
    BoxButtonDescription(
        key="resume",
        translation_key="resume",
        press_fn=lambda live, serial: live.async_resume(serial),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MeowantConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        MeowantButton(coordinator, serial, description) for serial in coordinator.data.boxes for description in BUTTONS
    )


class MeowantButton(MeowantLiveEntity, ButtonEntity):
    entity_description: BoxButtonDescription

    def __init__(self, coordinator: MeowantCoordinator, serial: str, description: BoxButtonDescription) -> None:
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator.live, self._serial)
