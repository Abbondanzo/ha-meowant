"""Parsed, typed views of the Meowant cloud responses. See API.md "Box state".

Like api.py and const.py, this module has no homeassistant dependency so it
can be unit-tested without installing homeassistant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Record eventType codes (see API.md). 47 is inferred from timing, not confirmed.
EVENT_CAT_VISIT = 21
EVENT_SCHEDULED_CLEAN = 33
EVENT_AUTO_CLEAN = 47
CLEAN_EVENT_TYPES = frozenset({EVENT_SCHEDULED_CLEAN, EVENT_AUTO_CLEAN})


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_ms(value: Any) -> datetime | None:
    ms = _int(value)
    return datetime.fromtimestamp(ms / 1000, tz=UTC) if ms else None


@dataclass(frozen=True, slots=True)
class Record:
    """One entry from record/new: a cat visit or a device event."""

    record_id: int
    event_type: int
    time: datetime  # from eventTime; never use timeStr (it's UTC+8)
    cat_id: int | None  # None when the box didn't match the visit to a cat
    weight_kg: float | None
    duration_s: int | None

    @classmethod
    def parse(cls, raw: Any) -> Record | None:
        if not isinstance(raw, dict):
            return None
        record_id, event_type, time = _int(raw.get("recordId")), _int(raw.get("eventType")), _time_ms(raw.get("eventTime"))
        if record_id is None or event_type is None or time is None:
            return None
        return cls(
            record_id=record_id,
            event_type=event_type,
            time=time,
            cat_id=_int(raw.get("catInfoId")) or None,
            weight_kg=_float(raw.get("weight")),
            duration_s=_int(raw.get("costTime")),
        )


def parse_records(raw: Any) -> list[Record]:
    """Records from a record/new page, newest first."""
    items = raw.get("records") if isinstance(raw, dict) else None
    records = [r for r in (Record.parse(item) for item in items or []) if r is not None]
    return sorted(records, key=lambda r: r.time, reverse=True)


@dataclass(frozen=True, slots=True)
class WasteBin:
    uses: int | None
    capacity: int | None
    alert_at: int | None
    percent: int | None

    @classmethod
    def parse(cls, raw: Any) -> WasteBin | None:
        if not isinstance(raw, dict):
            return None
        return cls(
            uses=_int(raw.get("boxUseTimes")),
            capacity=_int(raw.get("boxFullMax")),
            alert_at=_int(raw.get("boxFullAlert")),
            percent=_int(raw.get("percentage")),
        )

    @property
    def needs_emptying(self) -> bool | None:
        if self.uses is None or self.alert_at is None:
            return None
        return self.uses >= self.alert_at


@dataclass(frozen=True, slots=True)
class Deodorant:
    days_left: int | None
    expired: bool | None
    alert_enabled: bool | None

    @classmethod
    def parse(cls, raw: Any) -> Deodorant | None:
        if not isinstance(raw, dict):
            return None
        expired = raw.get("expired")
        alert = _int(raw.get("alertSwitch"))
        return cls(
            days_left=_int(raw.get("remainingDays")),
            expired=expired if isinstance(expired, bool) else None,
            alert_enabled=None if alert is None else bool(alert),
        )


@dataclass(frozen=True, slots=True)
class Cat:
    """A cat profile from cat/info. Account-wide, not tied to one box."""

    cat_id: int
    name: str
    weight_kg: float | None
    base_weight_kg: float | None
    visits_today: int | None  # cat/info "number"; meaning inferred, unverified
    time_in_box_today_s: int | None  # cat/info "costTime"; meaning inferred, unverified
    breed: str | None
    birthday: str | None

    @classmethod
    def parse(cls, raw: Any) -> Cat | None:
        if not isinstance(raw, dict) or (cat_id := _int(raw.get("catInfoId"))) is None:
            return None
        breed = raw.get("catBreed") if isinstance(raw.get("catBreed"), dict) else {}
        return cls(
            cat_id=cat_id,
            name=str(raw.get("nickname") or f"Cat {cat_id}"),
            weight_kg=_float(raw.get("weight")),
            base_weight_kg=_float(raw.get("baseWeight")),
            visits_today=_int(raw.get("number")),
            time_in_box_today_s=_int(raw.get("costTime")),
            breed=breed.get("breed") or None,  # English when X-UBT-Language: en is sent
            birthday=raw.get("birthday") or None,
        )


def parse_cats(raw: Any) -> dict[int, Cat]:
    cats = [c for c in (Cat.parse(item) for item in raw or []) if c is not None] if isinstance(raw, list) else []
    return {cat.cat_id: cat for cat in cats}


@dataclass(slots=True)
class BoxState:
    """Everything read about one litter box in a single poll.

    A section is None when its endpoint failed this poll, so entities that
    depend on it can go unavailable without taking the rest down.
    """

    serial: str
    name: str
    shared: bool
    online: bool | None = None
    firmware: str | None = None
    auto_clean_delay_s: int | None = None
    waste_bin: WasteBin | None = None
    deodorant: Deodorant | None = None
    cat_visits: list[Record] | None = None
    device_events: list[Record] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(
        cls,
        device: dict[str, Any],
        *,
        config: Any = None,
        waste_bin: Any = None,
        deodorant: Any = None,
        online: Any = None,
        cat_visits: Any = None,
        device_events: Any = None,
    ) -> BoxState:
        state = cls(
            serial=str(device["serialNumber"]),
            name=str(device.get("deviceName") or "Litter box"),
            shared=bool(device.get("shareFlag")),
            waste_bin=WasteBin.parse(waste_bin),
            deodorant=Deodorant.parse(deodorant),
            cat_visits=parse_records(cat_visits) if cat_visits is not None else None,
            device_events=parse_records(device_events) if device_events is not None else None,
            raw={
                "device": device,
                "config": config,
                "waste_bin": waste_bin,
                "deodorant": deodorant,
                "online": online,
                "cat_visits": cat_visits,
                "device_events": device_events,
            },
        )
        if isinstance(online, dict) and isinstance(online.get("online"), bool):
            state.online = online["online"]
        if isinstance(config, dict):
            state.auto_clean_delay_s = _int(config.get("lazyTime"))
            functions = config.get("boxFunctionVOList")
            if isinstance(functions, list) and functions and isinstance(functions[0], dict):
                state.firmware = functions[0].get("firmwareVersion") or None
        return state

    @property
    def last_visit(self) -> Record | None:
        return self.cat_visits[0] if self.cat_visits else None

    @property
    def last_event(self) -> Record | None:
        return self.device_events[0] if self.device_events else None

    @property
    def last_clean(self) -> Record | None:
        return next((r for r in self.device_events or [] if r.event_type in CLEAN_EVENT_TYPES), None)

    @property
    def records(self) -> list[Record]:
        """Visits and device events together, newest first."""
        return sorted([*(self.cat_visits or []), *(self.device_events or [])], key=lambda r: r.time, reverse=True)
