"""Sensors for Meowant litter boxes and cats."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfMass, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MeowantConfigEntry
from .coordinator import MeowantCoordinator
from .entity import MeowantBoxEntity, MeowantCatEntity, MeowantLiveEntity
from .im_proto import WorkState
from .models import BoxState, Cat, Record

EVENT_NAMES = {21: "cat_visit", 33: "scheduled_clean", 47: "auto_clean"}


@dataclass(frozen=True, kw_only=True)
class BoxSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BoxState], Any]
    attrs_fn: Callable[[BoxState, MeowantCoordinator], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class CatSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Cat], Any]


def _record_time(record: Record | None) -> datetime | None:
    return record.time if record else None


def _visit_attrs(box: BoxState, coordinator: MeowantCoordinator) -> dict[str, Any]:
    visit = box.last_visit
    if not visit:
        return {}
    cat = coordinator.data.cats.get(visit.cat_id) if visit.cat_id else None
    return {
        "cat": cat.name if cat else None,
        "cat_id": visit.cat_id,
        "weight_kg": visit.weight_kg,
        "duration_s": visit.duration_s,
    }


def _event_attrs(record: Record | None) -> dict[str, Any]:
    if not record:
        return {}
    return {"event": EVENT_NAMES.get(record.event_type, "other"), "event_code": record.event_type}


BOX_SENSORS: tuple[BoxSensorDescription, ...] = (
    BoxSensorDescription(
        key="waste_bin_level",
        translation_key="waste_bin_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda box: box.waste_bin.percent if box.waste_bin else None,
    ),
    BoxSensorDescription(
        key="waste_bin_uses",
        translation_key="waste_bin_uses",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda box: box.waste_bin.uses if box.waste_bin else None,
        attrs_fn=lambda box, _: (
            {"capacity": box.waste_bin.capacity, "alert_at": box.waste_bin.alert_at} if box.waste_bin else {}
        ),
    ),
    BoxSensorDescription(
        key="deodorant_days_left",
        translation_key="deodorant_days_left",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda box: box.deodorant.days_left if box.deodorant else None,
    ),
    BoxSensorDescription(
        key="last_cat_visit",
        translation_key="last_cat_visit",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda box: _record_time(box.last_visit),
        attrs_fn=_visit_attrs,
    ),
    BoxSensorDescription(
        key="last_visit_weight",
        translation_key="last_visit_weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda box: box.last_visit.weight_kg if box.last_visit else None,
    ),
    BoxSensorDescription(
        key="last_visit_duration",
        translation_key="last_visit_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda box: box.last_visit.duration_s if box.last_visit else None,
    ),
    BoxSensorDescription(
        key="last_clean",
        translation_key="last_clean",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda box: _record_time(box.last_clean),
        attrs_fn=lambda box, _: _event_attrs(box.last_clean),
    ),
    BoxSensorDescription(
        key="last_device_event",
        translation_key="last_device_event",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda box: _record_time(box.last_event),
        attrs_fn=lambda box, _: _event_attrs(box.last_event),
    ),
    BoxSensorDescription(
        key="auto_clean_delay",
        translation_key="auto_clean_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda box: box.auto_clean_delay_s,
    ),
)

CAT_SENSORS: tuple[CatSensorDescription, ...] = (
    CatSensorDescription(
        key="weight",
        translation_key="cat_weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda cat: cat.weight_kg,
    ),
    CatSensorDescription(
        key="visits_today",
        translation_key="cat_visits_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda cat: cat.visits_today,
    ),
    CatSensorDescription(
        key="time_in_box_today",
        translation_key="cat_time_in_box_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda cat: cat.time_in_box_today_s,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MeowantConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        MeowantBoxSensor(coordinator, serial, description)
        for serial in coordinator.data.boxes
        for description in BOX_SENSORS
    )
    async_add_entities(MeowantStatusSensor(coordinator, serial) for serial in coordinator.data.boxes)

    # Cats can be added in the app later; pick them up on future polls.
    known_cats: set[int] = set()

    @callback
    def _add_new_cats() -> None:
        new = [cat_id for cat_id in coordinator.data.cats if cat_id not in known_cats]
        if not new:
            return
        known_cats.update(new)
        async_add_entities(
            MeowantCatSensor(coordinator, cat_id, description) for cat_id in new for description in CAT_SENSORS
        )

    _add_new_cats()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_cats))


class MeowantBoxSensor(MeowantBoxEntity, SensorEntity):
    entity_description: BoxSensorDescription

    def __init__(self, coordinator: MeowantCoordinator, serial: str, description: BoxSensorDescription) -> None:
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        box = self.box
        return self.entity_description.value_fn(box) if box else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        box = self.box
        if not box or not self.entity_description.attrs_fn:
            return None
        return self.entity_description.attrs_fn(box, self.coordinator)


class MeowantStatusSensor(MeowantLiveEntity, SensorEntity):
    """What the box is doing right now, from the live (MQTT) connection."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "resetting", "cleaning", "levelling", "replacing_litter", "raking_up", "paused", "blocked"]

    def __init__(self, coordinator: MeowantCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "status")

    @property
    def _state(self) -> WorkState | None:
        return self.coordinator.live.state(self._serial)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    @property
    def native_value(self) -> str | None:
        state = self._state
        return state.status if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Raw codes, since most aren't decoded yet (see API.md). Not the full
        # field dump: its timestamp changes every reply and would bloat history.
        state = self._state
        if state is None:
            return None
        return {"work_mode": state.work_mode, "work_state": state.work_state, "cause": state.cause}


class MeowantCatSensor(MeowantCatEntity, SensorEntity):
    entity_description: CatSensorDescription

    def __init__(self, coordinator: MeowantCoordinator, cat_id: int, description: CatSensorDescription) -> None:
        super().__init__(coordinator, cat_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        cat = self.cat
        return self.entity_description.value_fn(cat) if cat else None
