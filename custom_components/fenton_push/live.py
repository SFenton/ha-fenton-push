"""The Home Live Activity's content: what ``mobile_app`` posts becomes the ActivityKit ``content-state``.

Contract v2, section 6 of ``docs/CONTRACT.md``. The shape is the Fenton Home app's ``HomeActivityState``
(``FentonActivityModel``): ``{"v": 1, "focus"?, "url"?, "jobs": [...]}``. This module has no Home Assistant imports, so
it is unit-tested alone, and it only ever *writes* what the app's decoder reads: unknown keys are dropped, numbers
stay numbers (ActivityKit decodes pushes with a default ``JSONDecoder``, so dates are Unix seconds, never strings),
and everything is cut to the budget that keeps a push under Apple's 4 KB.
"""

from __future__ import annotations

from typing import Any

#: The Swift type name; a push-to-start token is per attributes type, so it is also the wire ``attributes-type``.
ATTRIBUTES_TYPE = "HomeActivityAttributes"
#: Bumped when the ``fenton`` block of a Live Activity payload changes shape (alerts and commands stay on v1).
LIVE_CONTRACT_VERSION = 2
#: ``HomeActivityState.currentVersion``.
STATE_VERSION = 1
#: The tag of the aggregate Home activity, which ``mobile_app`` keys the update token by.
HOME_TAG = "fenton-home"

MAX_JOBS = 6
MAX_CONTROLS = 2
MAX_STATUS = 40
MAX_TEXT = 40
MAX_URL = 200
#: Seconds past the earliest end before the views read "Updating…".
STALE_GRACE = 600
#: The stale window of a list with no end times.
OPEN_ENDED_STALE = 1800
#: ActivityKit keeps an ended activity on the Lock Screen for four hours at most.
MAX_DISMISS_AFTER = 4 * 3600

PHASES = frozenset({"running", "paused", "waiting", "attention", "stopping", "done"})
ROLES = frozenset({"normal", "destructive"})

#: SF Symbols for the companion app's ``notification_icon`` values worth mapping; anything else is a plain circle.
_ICONS = {
    "mdi:dishwasher": "dishwasher",
    "mdi:washing-machine": "washer",
    "mdi:tumble-dryer": "dryer",
    "mdi:fan": "fan",
    "mdi:robot-vacuum": "fan",
    "mdi:timer": "timer",
    "mdi:timer-outline": "timer",
    "mdi:sprinkler": "drop.fill",
    "mdi:water": "drop.fill",
    "mdi:bell": "bell.fill",
    "mdi:alarm": "alarm.fill",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value: Any, limit: int) -> str:
    """``value`` as text no longer than ``limit``, ending in an ellipsis when cut."""
    text = "" if value is None else str(value).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _seconds(value: Any) -> int | None:
    """Unix seconds from a number (milliseconds are recognised and converted), else None."""
    if not _is_number(value) or value <= 0:
        return None
    return int(value / 1000) if value > 1e11 else int(value)


def _control(raw: Any) -> dict[str, str] | None:
    """A control as the phone may see it: id, title, role. The service it maps to never leaves Home Assistant."""
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
        return None
    control = {"id": _text(raw["id"], MAX_TEXT), "title": _text(raw["title"], MAX_TEXT)}
    if raw.get("role") in ROLES and raw["role"] != "normal":
        control["role"] = raw["role"]
    return control


def _job(raw: Any, notes: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
        notes.append("job without an id or title dropped")
        return None
    phase = raw.get("phase")
    if phase not in PHASES:
        if phase is not None:
            notes.append(f"unknown phase {phase!r} read as running")
        phase = "running"
    job: dict[str, Any] = {
        "id": _text(raw["id"], MAX_TEXT),
        "kind": _text(raw.get("kind") or "generic", MAX_TEXT),
        "title": _text(raw["title"], MAX_TEXT),
        "status": _text(raw.get("status"), MAX_STATUS),
        "phase": phase,
        "symbol": _text(raw.get("symbol") or "circle", MAX_TEXT),
        "tint": _text(raw.get("tint") or "neutral", MAX_TEXT),
    }
    for key in ("start_ts", "end_ts"):
        if (seconds := _seconds(raw.get(key))) is not None:
            job[key] = seconds
    if raw.get("estimated") is True:
        job["estimated"] = True
    if _is_number(raw.get("progress")):
        job["progress"] = round(min(max(float(raw["progress"]), 0.0), 1.0), 3)
    controls = [c for c in (_control(c) for c in (raw.get("controls") or [])) if c]
    if len(controls) > MAX_CONTROLS:
        notes.append("more than 2 controls on a job; extra ones dropped")
    if controls:
        job["controls"] = controls[:MAX_CONTROLS]
    return job


def home_state(block: Any, notes: list[str]) -> tuple[dict[str, Any], int | None]:
    """Validate an automation's ``fenton_activity`` block into a ``content-state``.

    Returns the state and the block's ``stale_ts`` (Unix seconds), which belongs in ``aps.stale-date`` rather than in
    the state. Raises ``ValueError`` when the block is not an object with a list of jobs.
    """
    if not isinstance(block, dict) or not isinstance(block.get("jobs"), list):
        raise ValueError("fenton_activity must be an object with a jobs list")
    jobs = [j for j in (_job(raw, notes) for raw in block["jobs"]) if j]
    focus = block.get("focus")
    if len(jobs) > MAX_JOBS:
        notes.append("more than 6 jobs; extra ones dropped")
        kept = jobs[:MAX_JOBS]
        focused = next((j for j in jobs if j["id"] == focus), None)
        if focused and focused not in kept:
            kept[-1] = focused
        jobs = kept
    state: dict[str, Any] = {"v": STATE_VERSION, "jobs": jobs}
    if focus and any(j["id"] == focus for j in jobs):
        state["focus"] = focus
    if isinstance(block.get("url"), str) and block["url"]:
        url = block["url"].strip()
        if len(url) > MAX_URL or "token=" in url.lower():
            notes.append("url dropped (too long, or it carries a token)")
        else:
            state["url"] = url
    return state, _seconds(block.get("stale_ts"))


def generic_state(tag: str, title: str, message: str, data: dict[str, Any], now: int, notes: list[str]) -> dict[str, Any]:
    """One ``generic`` job from the companion app's ``live_update`` keys, so existing automations show something sensible.

    ``message`` is the status line, ``progress``/``progress_max`` the bar, ``chronometer`` with ``when`` (or
    ``when_relative``) a countdown. A count-up chronometer (a time already past) is not drawn.
    """
    job: dict[str, Any] = {
        "id": _text(tag, MAX_TEXT),
        "kind": "generic",
        "title": _text(title or "Home", MAX_TEXT),
        "status": _text(message, MAX_STATUS),
        "phase": "running",
        "symbol": _ICONS.get(str(data.get("notification_icon") or ""), "circle"),
        "tint": "neutral",
    }
    if data.get("chronometer") and _is_number(data.get("when")):
        end = now + int(data["when"]) if data.get("when_relative") else _seconds(data["when"])
        if end is not None and end > now:
            job["start_ts"] = now
            job["end_ts"] = end
    progress, maximum = data.get("progress"), data.get("progress_max")
    if _is_number(progress) and _is_number(maximum) and maximum > 0:
        job["progress"] = round(min(max(progress / maximum, 0.0), 1.0), 3)
    state: dict[str, Any] = {"v": STATE_VERSION, "jobs": [job]}
    if isinstance(data.get("url"), str) and data["url"].strip() and "token=" not in data["url"].lower():
        state["url"] = data["url"].strip()[:MAX_URL]
    return state


def empty_state() -> dict[str, Any]:
    """The content of an activity that ends with nothing left to show."""
    return {"v": STATE_VERSION, "jobs": []}


def default_stale(state: dict[str, Any], now: int) -> int:
    """Earliest end of a running job plus ten minutes, or half an hour from now when no job has an end."""
    ends = [j["end_ts"] for j in state["jobs"] if j.get("phase") != "done" and "end_ts" in j]
    return max(min(ends), now) + STALE_GRACE if ends else now + OPEN_ENDED_STALE
