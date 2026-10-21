"""Activity event entity: fires when a new cat visit or clean shows up (read-only)."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MeowantConfigEntry
from .coordinator import MeowantCoordinator
from .entity import MeowantBoxEntity
from .models import EVENT_AUTO_CLEAN, EVENT_CAT_VISIT, EVENT_SCHEDULED_CLEAN, Record

EVENT_TYPES = {
    EVENT_CAT_VISIT: "cat_visit",
    EVENT_SCHEDULED_CLEAN: "scheduled_clean",
    EVENT_AUTO_CLEAN: "auto_clean",
}
OTHER = "other"


async def async_setup_entry(
    hass: HomeAssistant, entry: MeowantConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(MeowantActivityEvent(coordinator, serial) for serial in coordinator.data.boxes)


class MeowantActivityEvent(MeowantBoxEntity, EventEntity):
    """One event per new record (cat visit, scheduled clean, auto clean, ...)."""

    _attr_translation_key = "activity"
    _attr_event_types = [*EVENT_TYPES.values(), OTHER]

    def __init__(self, coordinator: MeowantCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "activity")

    @callback
    def _handle_coordinator_update(self) -> None:
        new = self.coordinator.data.new_records.get(self._serial, [])
        for record in new:  # oldest first, so the entity ends on the latest event
            self._trigger_event(EVENT_TYPES.get(record.event_type, OTHER), self._attributes(record))
            self.async_write_ha_state()
        if not new:
            super()._handle_coordinator_update()

    def _attributes(self, record: Record) -> dict:
        cat = self.coordinator.data.cats.get(record.cat_id) if record.cat_id else None
        attrs = {"time": record.time.isoformat(), "event_code": record.event_type, "record_id": record.record_id}
        if record.event_type == EVENT_CAT_VISIT:
            attrs |= {
                "cat": cat.name if cat else None,
                "cat_id": record.cat_id,
                "weight_kg": record.weight_kg,
                "duration_s": record.duration_s,
            }
        return attrs
