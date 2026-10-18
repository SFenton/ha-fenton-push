"""Job adapters on recorded state snapshots (spec sections 4 and 4a)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fenton_push import entities
from fenton_push.jobs import (
    Ctx,
    Memory,
    derive,
    hot_flash_audience,
    note_hot_flash,
    note_sessions,
    relevance_countdown,
    signals,
)
from fenton_push.snapshot import States, parse_time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scenarios as sc  # noqa: E402

NOW = sc.NOW


def jobs(snapshot: States, **kwargs):
    return {j.job_id: j for j in derive(snapshot, NOW, sc.ctx(**kwargs))}


# -- Fans -------------------------------------------------------------------------------------------------------------


def test_fan_counts_down_to_ends_at_with_a_ring_from_when_the_timer_started() -> None:
    job = jobs(sc.fan("guest"))["fan.guest"]
    assert job.title == "Guest Bath Fan" and job.tag == "job.fan.guest" and job.phase == "running"
    assert job.value == {"kind": "countdown", "end_ts": NOW + 1500, "start_ts": NOW - 300}
    assert [c.id for c in job.controls] == ["stop"] and job.headline is None
    assert job.relevance == 80 - 25 / 2


def test_fan_stop_turns_the_fan_off_and_waits_for_the_timer_flag() -> None:
    control = jobs(sc.fan("master"))["fan.master"].controls[0]
    (call,) = control.calls
    assert (call.domain, call.service) == ("script", "master_bathroom_fan_command")
    assert call.data == {"command": "power", "target_power": "off"}
    assert control.expect[0].entity_id == "input_boolean.master_bathroom_fan_timer_pending"
    assert control.expect[0].met("off") and not control.expect[0].met("on")


def test_fan_needs_both_the_flag_and_a_future_end() -> None:
    # ``ends_at`` goes stale after a cancel, so the flag alone is not enough...
    assert "fan.guest" not in jobs(sc.fan("guest", ends_in=-30))
    # ...and a future end with the flag off is a leftover.
    off = sc.fan("guest").with_(input_boolean__guest_bathroom_fan_timer_pending="off")
    assert "fan.guest" not in jobs(off)
    # Both fans can run at once.
    both = sc.fan("guest").with_(**{
        "input_boolean__master_bathroom_fan_timer_pending": ("on", {}, NOW - 10),
        "input_datetime__master_bathroom_fan_timer_ends_at": ("x", {"timestamp": NOW + 600})})
    assert {"fan.guest", "fan.master"} <= set(jobs(both))


def test_fan_reads_a_text_ends_at_in_local_time_when_there_is_no_timestamp_attribute() -> None:
    snapshot = sc.states(input_boolean__guest_bathroom_fan_timer_pending=("on", {}, NOW - 5),
                         input_datetime__guest_bathroom_fan_timer_ends_at="2026-10-17 15:25:00")
    assert jobs(snapshot)["fan.guest"].value["end_ts"] == NOW + 1500  # the test context is UTC


# -- Dishwasher -------------------------------------------------------------------------------------------------------


def test_dishwasher_counts_down_to_the_finish_time() -> None:
    job = jobs(sc.dishwasher())["dishwasher"]
    assert job.value == {"kind": "countdown", "end_ts": NOW + 2520, "start_ts": NOW - 8000}
    assert [c.id for c in job.controls] == ["stop"] and job.tolerance.end_seconds == 60


def test_dishwasher_falls_back_to_percent_then_to_text() -> None:
    percent = jobs(sc.dishwasher(finish_in=None, progress=62))["dishwasher"]
    assert percent.value == {"kind": "percent"} and percent.progress == 0.62
    plain = jobs(sc.dishwasher(finish_in=None))["dishwasher"]
    assert plain.value == {"kind": "text", "text": "Running"} and plain.progress is None
    # A finish time already past counts as unavailable.
    assert jobs(sc.dishwasher(finish_in=-60, progress=90))["dishwasher"].value == {"kind": "percent"}


def test_dishwasher_stop_only_while_remote_control_is_on() -> None:
    assert jobs(sc.dishwasher(remote=False))["dishwasher"].controls == ()
    stop = jobs(sc.dishwasher())["dishwasher"].controls[0]
    assert (stop.calls[0].domain, stop.calls[0].service) == ("button", "press")
    assert stop.calls[0].data == {"entity_id": "button.dishwasher_stop_program"}
    assert stop.expect[0].met("ready") and stop.expect[0].met("inactive") and not stop.expect[0].met("run")


def test_dishwasher_pause_offers_resume_then_stop() -> None:
    job = jobs(sc.dishwasher("pause"))["dishwasher"]
    assert job.phase == "paused" and [c.id for c in job.controls] == ["resume", "stop"]
    assert job.controls[0].calls[0].data == {"entity_id": "button.dishwasher_resume_program"}
    assert job.controls[0].outcome == "update"


def test_dishwasher_delayed_start_waits() -> None:
    job = jobs(sc.dishwasher("delayedstart"))["dishwasher"]
    assert job.phase == "waiting" and job.value == {"kind": "text", "text": "Delayed start"}
    later = sc.dishwasher("delayedstart").with_(sensor__dishwasher_program_start_time=sc.iso(NOW + 8400))
    assert jobs(later)["dishwasher"].value == {"kind": "text", "text": "Starts 5:20 PM"}


@pytest.mark.parametrize("op", ["ready", "inactive", "aborting", "unavailable"])
def test_dishwasher_stops_end_the_card_at_once(op) -> None:
    assert "dishwasher" not in jobs(sc.dishwasher(op))


def test_dishwasher_finished_is_complete() -> None:
    job = jobs(sc.dishwasher("finished"))["dishwasher"]
    assert job.complete and job.phase == "done" and job.value == {"kind": "text", "text": "Complete"}
    assert job.relevance == 10 and job.controls == () and job.progress == 1.0


# -- Laundry ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("job_id", "title"), [("dryer", "Dryer"), ("washer", "Washing Machine")])
def test_laundry_counts_up_from_five_minutes_before_the_helper_turned_on(job_id, title) -> None:
    job = jobs(sc.laundry(job_id, on_for=1800))[job_id]
    assert job.title == title and job.value == {"kind": "countup", "start_ts": NOW - 1800 - 300}
    assert job.controls == () and job.progress == 1.0 and job.relevance == 55 and job.complete_on_drop
    assert job_id not in jobs(sc.laundry(job_id, on=False))


# -- Vacuums ----------------------------------------------------------------------------------------------------------


def test_main_floor_room_headline_and_count_up() -> None:
    job = jobs(sc.main_floor())["vacuum.main_floor"]
    assert job.title == "Main Floor Vacuum" and job.headline == "Cleaning Kitchen"
    assert job.value == {"kind": "countup", "start_ts": NOW - 900} and job.session_key == "room:kitchen"
    assert [c.id for c in job.controls] == ["pause", "dock"] and job.progress == 1.0


@pytest.mark.parametrize(("mode", "verb"), [("vacuum", "Vacuuming"), ("mop", "Mopping"), ("vacuum_and_mop", "Cleaning")])
def test_main_floor_verb_follows_the_mode_unless_vacuum_only(mode, verb) -> None:
    assert jobs(sc.main_floor(mode=mode))["vacuum.main_floor"].headline == f"{verb} Kitchen"
    assert jobs(sc.main_floor(mode="mop", vacuum_only=True))["vacuum.main_floor"].headline == "Vacuuming Kitchen"


def test_main_floor_names_a_room_by_its_last_cleaned_sensor_or_its_id() -> None:
    snapshot = sc.main_floor(room_id="living_room")
    assert jobs(snapshot)["vacuum.main_floor"].headline == "Cleaning Living Room"


def test_coordinator_pause_and_dock_use_the_coordinator_services() -> None:
    job = jobs(sc.main_floor())["vacuum.main_floor"]
    pause, dock = job.controls
    assert pause.calls[0].domain == "valetudo_vacuum_coordinator" and pause.calls[0].service == "set_paused"
    assert pause.calls[0].data["paused"] is True and pause.expect[0].met("paused")
    assert dock.calls[0].service == "cancel_session" and dock.expect[0].met("returning") and dock.expect[0].met("docked")


def test_paused_room_freezes_the_time_and_offers_resume() -> None:
    job = jobs(sc.main_floor("paused", changed=NOW - 300))["vacuum.main_floor"]
    assert job.phase == "paused" and job.value == {"kind": "countup", "start_ts": NOW - 900, "paused_elapsed_s": 600}
    resume = job.controls[0]
    assert resume.id == "resume" and resume.calls[0].data["paused"] is False and resume.outcome == "update"


def test_a_room_change_is_a_new_session_and_between_rooms_there_is_no_card() -> None:
    assert jobs(sc.main_floor(room_id="hall"))["vacuum.main_floor"].session_key == "room:hall"
    assert "vacuum.main_floor" not in jobs(sc.main_floor(room_id="", phase="idle"))
    assert "vacuum.main_floor" not in jobs(sc.main_floor("returning"))
    assert "vacuum.main_floor" not in jobs(sc.main_floor("docked", phase="cleaning"))


def test_resumed_cleaning_counts() -> None:
    assert "vacuum.main_floor" in jobs(sc.main_floor(phase="resumed_cleaning"))
    assert "vacuum.main_floor" not in jobs(sc.main_floor(phase="travelling"))


@pytest.mark.parametrize(("flag", "attrs", "headline"), [
    ("segment", {"segment_name": "Hall"}, "Vacuuming Hall"),
    ("segment", {}, "Vacuuming"),
    ("zone", {}, "Cleaning Music Room area"),
    ("spot", {}, "Spot cleaning"),
    ("none", {}, "Cleaning Music Room"),
])
def test_manual_runs_name_what_the_status_flag_says(flag, attrs, headline) -> None:
    job = jobs(sc.manual_run("music_room", flag=flag, **attrs))["vacuum.music_room"]
    assert job.headline == headline
    assert job.value == {"kind": "countup", "start_ts": NOW - 300}
    # Plain vacuum services, no coordinator.
    assert job.controls[0].calls[0].domain == "vacuum" and job.controls[0].calls[0].service == "pause"
    assert job.controls[1].calls[0].service == "return_to_base"


def test_manual_run_resume_uses_vacuum_start_and_keeps_its_start_across_a_pause() -> None:
    memory = Memory()
    running = jobs(sc.manual_run("music_room", flag="spot"), memory=memory)["vacuum.music_room"]
    note_sessions(memory, [running])
    paused = jobs(sc.manual_run("music_room", flag="spot", vac="paused").with_(
        vacuum__valetudo_music_room=("paused", {}, NOW - 60)), memory=memory)["vacuum.music_room"]
    assert paused.value == {"kind": "countup", "start_ts": NOW - 300, "paused_elapsed_s": 240}
    assert paused.controls[0].calls[0].service == "start"
    # A different segment is a new session and starts its own timer.
    note_sessions(memory, [paused])
    other = jobs(sc.manual_run("music_room", flag="segment", segment_name="Hall"), memory=memory)["vacuum.music_room"]
    assert other.session_key == "segment:Hall" and other.value["start_ts"] == NOW - 300


@pytest.mark.parametrize("vac", ["returning", "docked", "idle", "error"])
def test_manual_run_ends_when_the_robot_returns(vac) -> None:
    assert "vacuum.music_room" not in jobs(sc.manual_run("music_room", vac=vac))


def test_a_coordinator_robot_between_rooms_does_not_look_like_a_manual_run() -> None:
    snapshot = sc.main_floor("cleaning", room_id="", phase="idle", auto=True)
    assert "vacuum.main_floor" not in jobs(snapshot)
    manual = sc.main_floor("cleaning", room_id="", phase="idle", auto=False)
    assert jobs(manual)["vacuum.main_floor"].headline == "Cleaning Main Floor"


def test_note_sessions_forgets_runs_that_are_gone() -> None:
    memory = Memory()
    note_sessions(memory, jobs(sc.manual_run("music_room", flag="spot"), memory=memory).values())
    assert "vacuum.music_room" in memory.session_start
    note_sessions(memory, [])
    assert memory.session_start == {}


# -- Problem signals --------------------------------------------------------------------------------------------------


def found(snapshot: States):
    return {s.job_id: s for s in signals(snapshot, NOW, sc.ctx())}


def test_the_coordinators_current_issue_is_the_main_floors_problem() -> None:
    signal = found(sc.main_floor_issue("navigation.stuck"))["vacuum.main_floor"]
    assert (signal.code, signal.room, signal.since) == ("navigation.stuck", "Kitchen", NOW - 120)
    assert "vacuum.main_floor" not in found(sc.main_floor_issue("navigation.stuck", status="resolved"))
    assert "vacuum.main_floor" not in found(sc.main_floor())


def test_a_robot_without_a_coordinator_maps_its_own_error_text() -> None:
    assert found(sc.robot_error("Unknown error 12"))["vacuum.music_room"].code == "vendor.unknown_error_12"
    assert found(sc.robot_error("Unknown error 75"))["vacuum.music_room"].code == "power.critical_battery"
    assert found(sc.robot_error("Wheel tangled"))["vacuum.music_room"].code == "unmapped.wheel_tangled"
    assert found(sc.robot_error("Wheel tangled"))["vacuum.music_room"].raw == "Wheel tangled"
    assert "vacuum.music_room" not in found(sc.robot_error("No error"))


def test_the_vacuum_only_fallback_helper() -> None:
    snapshot = sc.main_floor().with_(input_boolean__main_floor_vacuum_fallback=("on", {}, NOW - 50),
                                     input_text__main_floor_vacuum_error_message="Mop pads not found")
    signal = found(snapshot)["vacuum.main_floor"]
    assert signal.code == "mop.vacuum_only_fallback" and signal.raw == "Mop pads not found"
    # A real issue wins over the fallback.
    both = sc.main_floor_issue("navigation.stuck").with_(input_boolean__main_floor_vacuum_fallback="on")
    assert found(both)["vacuum.main_floor"].code == "navigation.stuck"


@pytest.mark.parametrize(("op", "code"), [("actionrequired", "dishwasher.action_required"),
                                           ("error", "dishwasher.error")])
def test_dishwasher_problems(op, code) -> None:
    assert found(sc.dishwasher(op))["dishwasher"].code == code
    assert "dishwasher" not in found(sc.dishwasher("run"))


def test_offline_devices_raise_no_problem() -> None:
    assert found(sc.states(sensor__dishwasher_operation_state="unavailable")) == {}


# -- Hot flash --------------------------------------------------------------------------------------------------------


def cards(snapshot: States, memory: Memory | None = None, **kwargs):
    memory = memory or Memory()
    note_hot_flash(memory, snapshot)
    return {next(iter(j.audience)): j for j in derive(snapshot, NOW, sc.ctx(memory=memory, **kwargs))
            if j.job_id == "hot_flash"}, memory


def test_hot_flash_cooling_shows_text_and_a_ring_toward_56_degrees() -> None:
    snapshot = sc.hot_flash("cooling", "idle", temp=70)
    memory = Memory()
    note_hot_flash(memory, snapshot)  # the bed was 70 when it began
    cooled = snapshot.with_(climate__sleepypod_left=("heat_cool", {"current_temperature": 63.0}))
    note_hot_flash(memory, cooled)
    by_person, _ = cards(cooled, memory)
    job = by_person[sc.STEPHEN]
    assert job.value == {"kind": "text", "text": "Cooling"} and job.relevance == 70
    assert job.progress == 0.5 and job.headline == "Hot Flash · Your side"
    assert job.audience == frozenset({sc.STEPHEN}) and job.title == "Hot Flash"


def test_hot_flash_holding_counts_down_to_the_timer() -> None:
    by_person, _ = cards(sc.hot_flash("holding", "idle", left_ends_in=800))
    assert by_person[sc.STEPHEN].value == {"kind": "countdown", "end_ts": NOW + 800, "start_ts": NOW - 100}
    assert [c.id for c in by_person[sc.STEPHEN].controls] == ["stop"]


def test_hot_flash_stop_presses_the_sides_cancel_button() -> None:
    by_person, _ = cards(sc.hot_flash("holding", "idle"))
    stop = by_person[sc.STEPHEN].controls[0]
    assert [c.data["entity_id"] for c in stop.calls] == ["input_button.eight_sleep_stephen_cancel_hot_flash"]
    assert stop.expect[0].entity_id == "input_select.sleepypod_hot_flash_left_phase"
    assert stop.expect[0].met("idle") and not stop.expect[0].met("holding")


def test_hot_flash_started_by_the_other_person_says_so() -> None:
    by_person, _ = cards(sc.hot_flash("holding", "idle", by_left="steph"))
    assert set(by_person) == {sc.STEPHEN, sc.STEPHANIE}
    assert by_person[sc.STEPHEN].headline == "Hot Flash · Your side · by Steph"
    assert by_person[sc.STEPHANIE].headline == "Hot Flash · Stephen's side"


def test_hot_flash_started_by_an_automation_is_for_the_owner_only() -> None:
    by_person, _ = cards(sc.hot_flash("holding", "idle", by_left="unattributed"))
    assert set(by_person) == {sc.STEPHEN} and by_person[sc.STEPHEN].headline == "Hot Flash · Your side · by Home"
    junk, _ = cards(sc.hot_flash("holding", "idle", by_left="nobody"))
    assert set(junk) == {sc.STEPHEN}


def test_hot_flash_whole_bed_uses_the_later_end_and_stops_both_sides() -> None:
    snapshot = sc.hot_flash("holding", "holding", by_left="stephen", by_right="stephen", left_ends_in=800,
                            right_ends_in=860)
    by_person, _ = cards(snapshot)
    job = by_person[sc.STEPHEN]
    assert job.headline == "Hot Flash · Whole bed" and job.value["end_ts"] == NOW + 860
    assert len(job.controls[0].calls) == 2 and len(job.controls[0].expect) == 2
    # Steph owns the right side and Stephen asked for it: she sees her side, started by Stephen. "Hot Flash · " would
    # push the headline past 32 characters, so the line stands alone.
    assert by_person[sc.STEPHANIE].headline == "Your side · by Stephen"


def test_hot_flash_whole_bed_cooling_wins_until_both_hold() -> None:
    snapshot = sc.hot_flash("holding", "cooling", by_left="stephen", by_right="stephen")
    assert cards(snapshot)[0][sc.STEPHEN].value == {"kind": "text", "text": "Cooling"}


def test_hot_flash_restore_pending_is_the_natural_end() -> None:
    by_person, _ = cards(sc.hot_flash("restore_pending", "idle"))
    job = by_person[sc.STEPHEN]
    assert job.complete and job.linger_holds and job.value == {"kind": "text", "text": "Complete"}
    assert job.headline == "Hot Flash · Your side" and job.relevance == 10 and job.controls == ()


@pytest.mark.parametrize("phase", ["idle", "superseded", "unknown"])
def test_hot_flash_other_phases_show_nothing(phase) -> None:
    assert cards(sc.hot_flash(phase, phase))[0] == {}


def test_hot_flash_remembers_who_started_it_not_who_the_stale_helper_names_later() -> None:
    memory = Memory()
    note_hot_flash(memory, sc.hot_flash("cooling", "idle", by_left="steph"))
    assert memory.hot_flash_starter == {"left": "steph"}
    # The helper is stale between runs; changing it mid-run changes nothing.
    note_hot_flash(memory, sc.hot_flash("holding", "idle", by_left="stephen"))
    assert memory.hot_flash_starter == {"left": "steph"}
    note_hot_flash(memory, sc.hot_flash("idle", "idle"))
    assert memory.hot_flash_starter == {}


def test_hot_flash_solo_trip_removes_the_traveller_even_from_their_own_side() -> None:
    away = ("away", {"mode": "solo_trip", "traveller": "person.stephen_fenton"})
    by_person, _ = cards(sc.hot_flash("holding", "idle", by_left="steph", away=away))
    assert set(by_person) == {sc.STEPHANIE}
    away = ("away", {"mode": "solo_trip", "traveler": ["Stephanie"]})
    by_person, _ = cards(sc.hot_flash("holding", "holding", by_left="stephen", by_right="steph", away=away))
    assert set(by_person) == {sc.STEPHEN}
    # Another mode is no trip.
    home = ("away", {"mode": "home", "traveller": "person.stephen_fenton"})
    assert set(cards(sc.hot_flash("holding", "idle", away=home))[0]) == {sc.STEPHEN}


def test_hot_flash_vacation_leaves_only_the_person_who_started_it() -> None:
    by_person, _ = cards(sc.hot_flash("holding", "idle", by_left="steph", vacation=True))
    assert set(by_person) == {sc.STEPHANIE}
    assert cards(sc.hot_flash("holding", "idle", by_left="unattributed", vacation=True))[0] == {}


def test_hot_flash_audience_rules_directly() -> None:
    left = entities.SIDES["left"]
    assert hot_flash_audience(left, "stephen", set(), False) == {sc.STEPHEN}
    assert hot_flash_audience(left, "steph", set(), False) == {sc.STEPHEN, sc.STEPHANIE}
    assert hot_flash_audience(left, "steph", {sc.STEPHEN}, False) == {sc.STEPHANIE}
    assert hot_flash_audience(left, "steph", set(), True) == {sc.STEPHANIE}
    assert hot_flash_audience(left, "unattributed", set(), False) == {sc.STEPHEN}


# -- Everything -------------------------------------------------------------------------------------------------------


def test_disabled_jobs_are_not_derived() -> None:
    both = sc.fan("guest").with_(**{"sensor__dishwasher_operation_state": "run",
                                    "sensor__dishwasher_program_finish_time": sc.iso(NOW + 900)})
    assert set(jobs(both)) == {"fan.guest", "dishwasher"}
    assert set(jobs(both, enabled=frozenset({"dishwasher"}))) == {"dishwasher"}
    assert jobs(both, enabled=frozenset()) == {}
    assert signals(sc.dishwasher("error"), NOW, Ctx(enabled=frozenset())) == []


def test_relevance_for_countdowns_runs_from_80_down_to_50() -> None:
    assert relevance_countdown(NOW, NOW) == 80
    assert relevance_countdown(NOW + 600, NOW) == 75
    assert relevance_countdown(NOW + 3600, NOW) == 50
    assert relevance_countdown(NOW + 3 * 3600, NOW) == 50
    assert relevance_countdown(NOW - 100, NOW) == 80


def test_states_helpers() -> None:
    snap = States.build({"sensor.a": "5", "sensor.b": ("unavailable",), "sensor.c": ("x", {"k": None}, 3)})
    assert snap.number("sensor.a") == 5 and snap.number("sensor.b") is None and snap.number("sensor.missing") is None
    assert snap.known("sensor.b") is None and snap.state("sensor.missing") == "unavailable"
    assert snap.attr("sensor.c", "k", "dflt") == "dflt" and snap.changed("sensor.c") == 3
    assert snap.changed("sensor.a") is None and "sensor.a" in snap and snap.entity_ids() == ["sensor.a", "sensor.b", "sensor.c"]
    assert snap.number("sensor.c") is None


def test_parse_time() -> None:
    assert parse_time("2026-10-17T15:00:00Z") == NOW and parse_time("2026-10-17T15:00:00+00:00") == NOW
    assert parse_time(NOW * 1000) == NOW and parse_time(str(NOW)) == NOW
    assert parse_time("2026-10-17 15:00:00") == NOW
    assert parse_time(0) is None and parse_time(True) is None and parse_time("soon") is None and parse_time("") is None
    assert parse_time(None) is None and parse_time(-5) is None
