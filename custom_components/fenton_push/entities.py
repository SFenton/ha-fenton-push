"""The household's entity ids, wording and service mappings, in one place.

Everything the job engine knows about the owner's Home Assistant lives here as data, so a wrong entity id or
service name is a one-line fix and never a logic change. Names come from the Live Activities per-job spec
(``Fenton-HASS-Native/.agents/platforms/apple/live-activities-per-job.md``, sections 4, 4a, 5 and 7); the ones the
spec does not give (marked ``VERIFY``) are best guesses the planner checks against the real configuration before
deploying. No Home Assistant imports, so it is shared by the unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------------------------------------------------
# Timings and limits (spec sections 4, 6 and 7)

#: Seconds a job that finished on its own shows "Complete" before the card ends.
LINGER_SECONDS = 300
#: ActivityKit ends an activity after 8 hours; the engine ends and restarts it at 7 h 45 min.
MAX_AGE_SECONDS = 7 * 3600 + 45 * 60
#: At most one push per tag per device in this many seconds; a burst collapses to the latest state.
COALESCE_SECONDS = 2.0
#: Slots per device: five activities observed, four for jobs and one for a problem.
SLOTS_TOTAL = 5
SLOTS_JOBS = 4
#: A control waits this long for the device to reach the expected state before the card says "Couldn't ...".
CONFIRM_TIMEOUT = 15.0
#: Re-push an unchanged open-ended card this often so its stale date (30 min) never passes.
HEARTBEAT_SECONDS = 25 * 60
OPEN_ENDED_STALE_SECONDS = 30 * 60
#: After Home Assistant starts, cards the engine finds no job for are kept this long: other integrations' entities
#: (Home Connect, the vacuums) are not there yet, and ending every card would only start them again.
STARTUP_HOLD_SECONDS = 120.0
#: Seconds after the engine ends a card during which a "dismissed" report is the app echoing our own end.
DISMISS_ECHO_SECONDS = 15.0
#: How long a service-raised problem with no task and no sensor confirmation stays before it is dropped.
PROBLEM_GRACE_SECONDS = 120.0

# ---------------------------------------------------------------------------------------------------------------------
# Jobs

JOB_FAN_GUEST = "fan.guest"
JOB_FAN_MASTER = "fan.master"
JOB_DISHWASHER = "dishwasher"
JOB_DRYER = "dryer"
JOB_WASHER = "washer"
JOB_HOT_FLASH = "hot_flash"
JOB_VAC_MAIN = "vacuum.main_floor"
JOB_VAC_MUSIC = "vacuum.music_room"
JOB_VAC_THEATER = "vacuum.theater_room"

JOB_IDS = (JOB_FAN_GUEST, JOB_FAN_MASTER, JOB_DISHWASHER, JOB_DRYER, JOB_WASHER, JOB_VAC_MAIN, JOB_VAC_MUSIC,
           JOB_VAC_THEATER, JOB_HOT_FLASH)

TAG_PREFIX = "job."


def tag_for(job_id: str) -> str:
    return TAG_PREFIX + job_id


def job_for_tag(tag: str) -> str | None:
    return tag[len(TAG_PREFIX):] if tag.startswith(TAG_PREFIX) else None


@dataclass(frozen=True)
class Style:
    """The static attributes of a job's activity: what the card is called and how it is drawn."""

    title: str
    kind: str
    symbol: str
    tint: str
    url: str


# ``symbol`` is a glyph id from the app's own set and ``tint`` a ``ColorFamily`` raw value (spec section 3).
# VERIFY: the tint raw values other than ``air``, ``cleaning`` and ``water`` (the spec's examples), the glyph ids
# other than the spec's ``fan`` and ``vacuum``, and every dashboard ``path=`` except ``kitchen`` and ``vacuums``.
STYLES: dict[str, Style] = {
    JOB_FAN_GUEST: Style("Guest Bath Fan", "fan", "fan", "air", "/sfenton-react-dash/home?path=bathrooms"),
    JOB_FAN_MASTER: Style("Master Bath Fan", "fan", "fan", "air", "/sfenton-react-dash/home?path=bathrooms"),
    JOB_DISHWASHER: Style("Dishwasher", "dishwasher", "dishwasher", "water", "/sfenton-react-dash/home?path=kitchen"),
    JOB_DRYER: Style("Dryer", "dryer", "dryer", "warm", "/sfenton-react-dash/home?path=laundry"),
    JOB_WASHER: Style("Washing Machine", "washer", "washer", "water", "/sfenton-react-dash/home?path=laundry"),
    JOB_HOT_FLASH: Style("Hot Flash", "hot_flash", "hot_flash", "warm", "/sfenton-react-dash/home?path=bedroom"),
    JOB_VAC_MAIN: Style("Main Floor Vacuum", "vacuum", "vacuum", "cleaning", "/sfenton-react-dash/home?path=vacuums"),
    JOB_VAC_MUSIC: Style("Music Room Vacuum", "vacuum", "vacuum", "cleaning", "/sfenton-react-dash/home?path=vacuums"),
    JOB_VAC_THEATER: Style("Theater Room Vacuum", "vacuum", "vacuum", "cleaning",
                           "/sfenton-react-dash/home?path=vacuums"),
}

# ---------------------------------------------------------------------------------------------------------------------
# Bathroom fans (spec section 4)

FAN_ROOMS = {JOB_FAN_GUEST: "guest", JOB_FAN_MASTER: "master"}
#: Seconds the fan's ``ends_at`` may move before the card is updated.
FAN_END_TOLERANCE = 30
#: Stop is ``power off`` (spec section 4). If the planner finds that ``power off`` leaves the timer pending, set this and
#: Stop cancels the timer first, then turns the fan off.
FAN_STOP_CANCELS_TIMER_FIRST = False


def fan_entities(room: str) -> dict[str, str]:
    return {
        "pending": f"input_boolean.{room}_bathroom_fan_timer_pending",
        "ends_at": f"input_datetime.{room}_bathroom_fan_timer_ends_at",
        "command": f"{room}_bathroom_fan_command",  # script.<room>_bathroom_fan_command
    }


# ---------------------------------------------------------------------------------------------------------------------
# Dishwasher (spec section 4; entities verified 2026-10-17 in live-activities.md section 8)

DISHWASHER = {
    "state": "sensor.dishwasher_operation_state",
    "finish": "sensor.dishwasher_program_finish_time",
    "progress": "sensor.dishwasher_program_progress",
    "remote": "binary_sensor.dishwasher_remote_control",
    "stop": "button.dishwasher_stop_program",
    "resume": "button.dishwasher_resume_program",
    # VERIFY: only used for the "Starts h:mm" text of a delayed start; without it the card says "Delayed start".
    "start_time": "sensor.dishwasher_program_start_time",
}
DISHWASHER_END_TOLERANCE = 60
#: Progress moves this many points, at most this often, before a progress-only card is updated.
PROGRESS_STEP = 0.05
PROGRESS_INTERVAL = 300

# ---------------------------------------------------------------------------------------------------------------------
# Laundry: the started helpers turn on five minutes after the vibration starts (spec section 4)

LAUNDRY = {
    JOB_DRYER: "input_boolean.dryer_started_helper",
    JOB_WASHER: "input_boolean.washer_started_helper",
}
LAUNDRY_OFFSET = 300

# ---------------------------------------------------------------------------------------------------------------------
# Vacuums (spec sections 4 and 5)


@dataclass(frozen=True)
class Robot:
    job: str
    floor: str  # "Main Floor": used in "Cleaning Main Floor"
    vacuum: str  # the vacuum entity
    robot_id: str  # the Valetudo id in sensor.valetudo_<id>_status_flag and _error
    #: The coordinator's object id (``main_floor_vacuum_coordinator``), or None for a robot with no coordinator.
    coordinator: str | None = None
    #: The ``<floor>`` of ``sensor.<floor>_vacuum_status`` (the coordinator's problem sensor).
    status_floor: str | None = None
    mode_select: str | None = None
    fallback_helper: str | None = None
    error_message_helper: str | None = None

    @property
    def status_flag(self) -> str:
        return f"sensor.valetudo_{self.robot_id}_status_flag"

    @property
    def error_sensor(self) -> str:
        return f"sensor.valetudo_{self.robot_id}_error"


# VERIFY: every vacuum entity id and the music and theater robot ids (the spec only gives the Main Floor robot id,
# ``exaltedsneakydeer``, through ``select.valetudo_exaltedsneakydeer_mode``).
ROBOTS: dict[str, Robot] = {
    JOB_VAC_MAIN: Robot(
        job=JOB_VAC_MAIN, floor="Main Floor", vacuum="vacuum.valetudo_exaltedsneakydeer",
        robot_id="exaltedsneakydeer", coordinator="main_floor_vacuum_coordinator", status_floor="main_floor",
        mode_select="select.valetudo_exaltedsneakydeer_mode",
        fallback_helper="input_boolean.main_floor_vacuum_fallback",
        error_message_helper="input_text.main_floor_vacuum_error_message"),
    JOB_VAC_MUSIC: Robot(job=JOB_VAC_MUSIC, floor="Music Room", vacuum="vacuum.valetudo_music_room",
                         robot_id="music_room"),
    JOB_VAC_THEATER: Robot(job=JOB_VAC_THEATER, floor="Theater Room", vacuum="vacuum.valetudo_theater_room",
                           robot_id="theater_room"),
}

#: Coordinator room phases that mean a room is being cleaned right now.
COORDINATOR_ACTIVE_PHASES = frozenset({"cleaning", "resumed_cleaning"})
#: ``mode`` select option to the verb on the card.
MODE_VERBS = {"vacuum": "Vacuuming", "mop": "Mopping", "vacuum_and_mop": "Cleaning"}
#: ``current_issue.status`` values that mean the issue is over.
ISSUE_CLEARED = frozenset({"", "none", "cleared", "resolved", "ok", "closed"})
#: Vacuum states that end a manual run at once.
VACUUM_ENDED_STATES = frozenset({"returning", "docked", "idle", "error", "unavailable", "unknown"})


def coordinator_entities(robot: Robot) -> dict[str, str]:
    assert robot.coordinator
    return {
        "current_room": f"sensor.{robot.coordinator}_current_room",
        "auto_cleaning": f"binary_sensor.{robot.coordinator}_auto_cleaning",
        "status": f"sensor.{robot.status_floor}_vacuum_status",
    }


def last_cleaned_entity(robot: Robot, room_id: str) -> str:
    return f"sensor.{robot.coordinator}_{room_id}_last_cleaned"


#: Service calls for the vacuum controls. ``{entity_id}`` and friends are filled per robot by ``jobs``.
#: VERIFY: the coordinator integration's service names and field names (spec section 4 names the services only).
COORDINATOR_DOMAIN = "valetudo_vacuum_coordinator"
COORDINATOR_PAUSE_SERVICE = "set_paused"
COORDINATOR_CANCEL_SERVICE = "cancel_session"

# ---------------------------------------------------------------------------------------------------------------------
# Hot flash (spec section 4a)

#: ``hot_flash_active`` phases: the bed is cooling or holding; ``restore_pending`` is the natural end.
HOT_FLASH_ACTIVE = frozenset({"cooling", "holding"})
HOT_FLASH_FINISHED = frozenset({"restore_pending"})
HOT_FLASH_HOLD_SECONDS = 900
#: The coldest level the bed chills to (Fahrenheit), for the cooling ring.
HOT_FLASH_TARGET_TEMP = 56.0


@dataclass(frozen=True)
class Side:
    key: str  # left / right
    owner: str  # person entity id
    owner_key: str  # the value ``requested_by`` takes when the owner started it
    phase: str
    timer: str
    restore_at: str
    requested_by: str
    cancel: str
    climate: str


# VERIFY: the two ``climate`` entity ids (the spec says "the climate's current_temperature" without naming it).
SIDES: dict[str, Side] = {
    "left": Side("left", "person.stephen_fenton", "stephen", "input_select.sleepypod_hot_flash_left_phase",
                 "timer.sleepypod_hot_flash_left", "input_datetime.sleepypod_hot_flash_left_restore_at",
                 "input_text.eight_sleep_stephen_hot_flash_requested_by",
                 "input_button.eight_sleep_stephen_cancel_hot_flash", "climate.sleepypod_left"),
    "right": Side("right", "person.stephanie_hobart", "steph", "input_select.sleepypod_hot_flash_right_phase",
                  "timer.sleepypod_hot_flash_right", "input_datetime.sleepypod_hot_flash_right_restore_at",
                  "input_text.eight_sleep_steph_hot_flash_requested_by",
                  "input_button.eight_sleep_steph_cancel_hot_flash", "climate.sleepypod_right"),
}
#: ``requested_by`` value to the person it names, and the name shown on the card.
REQUESTERS = {"stephen": "person.stephen_fenton", "steph": "person.stephanie_hobart"}
REQUESTER_NAMES = {"stephen": "Stephen", "steph": "Steph"}
UNATTRIBUTED = "unattributed"
AWAY_STATUS = "sensor.household_away_status"
VACATION_MODE = "binary_sensor.effective_vacation_mode"
#: VERIFY: which attribute of ``sensor.household_away_status`` names the traveller during a Solo Trip. Each is tried
#: in turn; a value may be a person entity id, a first name or a list of either.
AWAY_TRAVELLER_ATTRIBUTES = ("traveller", "traveler", "traveling", "travelling", "away_person", "person", "persons")

# ---------------------------------------------------------------------------------------------------------------------
# Donetick (spec section 5)

TODO_ENTITY = "todo.all_tasks_internal"
#: HA user id to Donetick user id, for ``donetick.complete_task``'s ``completed_by``. Empty: not passed.
DONETICK_USERS: dict[str, int] = {}

# ---------------------------------------------------------------------------------------------------------------------
# Household notification fallback (spec section 5)

#: The notify action that reaches a person's whole phone (Companion included): ``notify.stephen_s_phone``, where ``*``
#: stands for the person's first name.
DEFAULT_PERSON_NOTIFY = "*_s_phone"


def style_for(job_id: str) -> Style:
    return STYLES.get(job_id) or Style(job_id, "problem", "alert", "neutral", "/sfenton-react-dash/home")


def resolve_device(device: str) -> str | None:
    """A ``fenton_push.problem`` ``device`` to a job id: ``main_floor``, ``vacuum.main_floor`` or ``dishwasher``."""
    name = (device or "").strip().lower().replace(" ", "_").replace("-", "_")
    if name.startswith(TAG_PREFIX):
        name = name[len(TAG_PREFIX):]
    if name in STYLES:
        return name
    if f"vacuum.{name}" in STYLES:
        return f"vacuum.{name}"
    for suffix in ("_vacuum", "_robot"):
        if name.endswith(suffix) and f"vacuum.{name[:-len(suffix)]}" in STYLES:
            return f"vacuum.{name[:-len(suffix)]}"
    if f"fan.{name}" in STYLES:
        return f"fan.{name}"
    return None


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()
