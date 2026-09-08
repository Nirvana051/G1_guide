"""The action state machine: the one real state machine, transition-tabled."""

from __future__ import annotations

import pytest

from g1_api.errors import ConflictError, FailureCode
from g1_api.models.enums import ActionLifecycle, ActionResult
from g1_api.state.action_state_machine import ActionStateMachine, compat_status_result


def test_status_result_mapping_skips_2_and_folds_terminal() -> None:
    assert compat_status_result(ActionLifecycle.PENDING) == (0, 0)
    assert compat_status_result(ActionLifecycle.RUNNING) == (1, 0)
    assert compat_status_result(ActionLifecycle.PAUSED) == (3, 0)
    assert compat_status_result(ActionLifecycle.SUCCEEDED) == (4, 0)
    assert compat_status_result(ActionLifecycle.FAILED) == (4, -1)
    assert compat_status_result(ActionLifecycle.CANCELLED) == (4, -2)


def test_happy_path() -> None:
    m = ActionStateMachine(action_id="a1")
    assert m.start() is ActionLifecycle.RUNNING
    assert m.succeed() is ActionLifecycle.SUCCEEDED
    assert m.result is ActionResult.SUCCESS
    assert m.is_terminal


def test_cancel_from_running() -> None:
    m = ActionStateMachine(action_id="a1")
    m.start()
    assert m.cancel() is ActionLifecycle.CANCELLED
    assert m.result is ActionResult.CANCELLED
    assert m.failure_code is FailureCode.CANCELLED_BY_USER


def test_fail_sets_failure_code_and_result() -> None:
    m = ActionStateMachine()
    m.start()
    m.fail(FailureCode.TIMEOUT, "too slow")
    assert m.is_terminal
    assert m.result is ActionResult.FAILED
    assert m.failure_code is FailureCode.TIMEOUT
    assert m.reason == "too slow"


def test_illegal_transition_raises_conflict() -> None:
    m = ActionStateMachine()
    m.start()
    m.succeed()
    with pytest.raises(ConflictError):
        m.start()


def test_terminal_has_no_outgoing() -> None:
    m = ActionStateMachine()
    m.fail(FailureCode.UNKNOWN)
    for target in (ActionLifecycle.RUNNING, ActionLifecycle.PAUSED, ActionLifecycle.SUCCEEDED):
        with pytest.raises(ConflictError):
            m.transition(target)


def test_pause_resume_cycle() -> None:
    m = ActionStateMachine()
    m.start()
    assert m.pause() is ActionLifecycle.PAUSED
    assert m.resume() is ActionLifecycle.RUNNING
    m.succeed()
