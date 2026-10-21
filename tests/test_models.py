"""Unit tests for custom_components/meowant/models.py (response parsing)."""
from __future__ import annotations

from datetime import UTC, datetime

from custom_components.meowant.models import (
    BoxState,
    Record,
    parse_cats,
    parse_records,
)

VISIT = {"recordId": 3, "eventType": 21, "eventTime": 1792550769000, "timeStr": "2026-10-21 10:46:09",
         "catInfoId": 0, "weight": 3.4, "costTime": 67}
CLEAN = {"recordId": 2, "eventType": 33, "eventTime": 1792533685000, "catInfoId": 0, "weight": 0, "costTime": 0}
AFTER_VISIT = {"recordId": 4, "eventType": 47, "eventTime": 1792551110000, "catInfoId": 0, "weight": 0, "costTime": 0}


def test_record_uses_event_time_not_timestr():
    record = Record.parse(VISIT)
    assert record.time == datetime.fromtimestamp(1792550769, tz=UTC)
    assert record.cat_id is None  # 0 means unmatched
    assert (record.weight_kg, record.duration_s) == (3.4, 67)


def test_record_rejects_incomplete():
    assert Record.parse({"recordId": 1}) is None
    assert Record.parse("nope") is None


def test_parse_records_newest_first():
    records = parse_records({"records": [CLEAN, AFTER_VISIT, {"bad": 1}]})
    assert [r.record_id for r in records] == [4, 2]


def test_box_state_sections_and_derived_values():
    box = BoxState.parse(
        {"serialNumber": "SN", "deviceName": "Box", "shareFlag": 1},
        config={"lazyTime": 60, "boxFunctionVOList": [{"firmwareVersion": "main_v1.0.0.43", "type": 1}]},
        waste_bin={"boxUseTimes": 20, "boxFullMax": 30, "boxFullAlert": 20, "percentage": 66},
        deodorant={"alertSwitch": 0, "remainingDays": 20, "expired": False},
        online={"online": True},
        cat_visits={"records": [VISIT]},
        device_events={"records": [CLEAN, AFTER_VISIT]},
    )
    assert (box.serial, box.shared, box.online, box.firmware, box.auto_clean_delay_s) == (
        "SN", True, True, "main_v1.0.0.43", 60,
    )
    assert box.waste_bin.needs_emptying is True
    assert (box.deodorant.days_left, box.deodorant.expired, box.deodorant.alert_enabled) == (20, False, False)
    assert box.last_visit.record_id == 3
    assert box.last_clean.record_id == 4
    assert [r.record_id for r in box.records] == [4, 3, 2]


def test_box_state_missing_sections_are_none():
    box = BoxState.parse({"serialNumber": "SN"})
    assert box.waste_bin is None and box.deodorant is None and box.online is None
    assert box.cat_visits is None and box.last_visit is None and box.last_clean is None


def test_parse_cats():
    cats = parse_cats([
        {"catInfoId": 5, "nickname": "Whiskers", "weight": 5.03, "baseWeight": 5, "number": 2, "costTime": 90,
         "catBreed": {"id": 78, "breed": "American Shorthair"}},
        {"nickname": "no id"},
    ])
    assert list(cats) == [5]
    cat = cats[5]
    assert (cat.name, cat.weight_kg, cat.visits_today, cat.time_in_box_today_s, cat.breed) == (
        "Whiskers", 5.03, 2, 90, "American Shorthair",
    )
