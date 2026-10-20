"""The app icon badge: the number Fenton Home's Profile button shows (overdue chores plus expired food).

Pure functions over a ``States`` snapshot, mirroring the app's ``ProfileSummary.badgeCount``. Only the household's two
residents have a count; anyone else gets 0 (as the app shows no Profile badge until the viewer is a resident).
"""

from __future__ import annotations

from datetime import date

from .snapshot import UNAVAILABLE, States

#: Home Assistant user id to the prefix of that resident's DoneTick lists (``ProfileSummary.lists(for:)``).
RESIDENT_LIST_PREFIX = {
    "64089b5683944c39b4f944c8f76830b0": "stephen_s",  # Stephen
    "43cb71bbd1cb4860b2a7de4c829020f0": "steph_s",  # Steph
}
EXPIRED_FOOD = "sensor.evershelf_expired_items"
VACATION_MODE = "input_boolean.vacation_mode"


def _open_count(states: States, entity_id: str) -> int:
    value = states.number(entity_id)
    return int(value) if value is not None and value > 0 else 0


def _expired_food(states: States, today: date) -> int:
    ent = states.get(EXPIRED_FOOD)
    if ent is None or ent.state in UNAVAILABLE:
        return 0
    items = ent.attrs.get("expired_list")
    if not isinstance(items, list):
        return 0
    count = 0
    for item in items:
        text = str(item.get("expiry_date") or "")[:10] if isinstance(item, dict) else ""
        try:
            expiry = date.fromisoformat(text)
        except ValueError:
            continue
        if (today - expiry).days > 0:
            count += 1
    return count


def badge_count(user_id: str, states: States, today: date) -> int | None:
    """The attention count for a Home Assistant user, or ``None`` when that user is not a resident."""
    prefix = RESIDENT_LIST_PREFIX.get((user_id or "").strip().lower())
    if prefix is None:
        return None
    overdue = _open_count(states, f"todo.{prefix}_past_due_with_unassigned")
    return overdue + (0 if states.is_on(VACATION_MODE) else _expired_food(states, today))


#: Entities a count reads.
ENTITY_IDS = [EXPIRED_FOOD, VACATION_MODE] + [f"todo.{p}_past_due_with_unassigned" for p in RESIDENT_LIST_PREFIX.values()]
