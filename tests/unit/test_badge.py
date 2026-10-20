"""The icon badge count mirrors the app's Profile button."""

from __future__ import annotations

from datetime import date

from fenton_push.badge import badge_count
from fenton_push.snapshot import States

STEPHEN = "64089b5683944c39b4f944c8f76830b0"
TODAY = date(2026, 10, 20)


def states(overdue="2", vacation="off", expired=None):
    expired = [{"expiry_date": "2026-10-19"}, {"expiry_date": "2026-10-20"}, {"expiry_date": "2026-10-01T00:00"}] if expired is None else expired
    return States.build({
        "todo.stephen_s_past_due_with_unassigned": overdue,
        "input_boolean.vacation_mode": vacation,
        "sensor.evershelf_expired_items": ("3", {"expired_list": expired}),
    })


def test_overdue_plus_locally_expired_food() -> None:
    assert badge_count(STEPHEN, states(), TODAY) == 4  # 2 + yesterday + Oct 1; today is not expired


def test_vacation_mode_holds_food_back() -> None:
    assert badge_count(STEPHEN, states(vacation="on"), TODAY) == 2


def test_unavailable_entities_count_zero() -> None:
    assert badge_count(STEPHEN, States.build({}), TODAY) == 0
    assert badge_count(STEPHEN, states(overdue="unavailable"), TODAY) == 2


def test_non_resident_has_no_count() -> None:
    assert badge_count("someone-else", states(), TODAY) is None
