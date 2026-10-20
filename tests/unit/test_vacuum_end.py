"""A vacuum card ends when the clean does, whichever way the robot finishes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fenton_push.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scenarios as sc  # noqa: E402


def reasons(engine, snapshot, at):
    return [(type(a).__name__, getattr(a, "reason", None)) for a in sc.run(engine, snapshot, sc.NOW + at).actions]


@pytest.mark.parametrize("finish", ["returning", "docked"])
def test_a_manual_run_ends_when_the_robot_heads_home(finish) -> None:
    engine = Engine()
    reasons(engine, sc.manual_run(), 0)
    assert reasons(engine, sc.manual_run(vac=finish), 60) == [("End", "gone")]


def test_a_manual_run_that_hit_an_error_still_ends_when_the_robot_docks() -> None:
    engine = Engine()
    reasons(engine, sc.manual_run(), 0)
    reasons(engine, sc.robot_issue("navigation.target_unreachable"), 30)
    assert reasons(engine, sc.manual_run(vac="returning"), 90) == [("End", "gone")]
    assert reasons(engine, sc.manual_run(vac="docked"), 150) == []


def test_a_coordinator_session_ends_when_the_robot_returns_even_if_the_room_is_stale() -> None:
    engine = Engine()
    reasons(engine, sc.main_floor(), 0)
    assert reasons(engine, sc.main_floor("returning"), 60) == [("End", "gone")]
