"""``SafetyGate`` policy: defaults false, named interlocks, all-or-nothing denial."""

from __future__ import annotations

import math
from typing import Optional

import pytest

from g1_api.config import SafetyConfig
from g1_api.core.clock import ManualClock
from g1_api.core.safety import (
    CommandClass,
    DenialKind,
    INTERLOCK_FOR_FLAG,
    REQUIRED_FLAG_BY_COMMAND_CLASS,
    SafetyContext,
    SafetyGate,
    command_class_for_action_kind,
)
from g1_api.errors import NotReadyError, SafetyInterlockError, UnsafeRequestError
from g1_api.models.action import ActionKind
from g1_api.models.state import Interlock

GATED_CLASSES = [
    (klass, flag)
    for klass, flag in REQUIRED_FLAG_BY_COMMAND_CLASS.items()
    if flag is not None
]


def _permissive() -> SafetyConfig:
    return SafetyConfig(
        allow_motion=True,
        allow_navigation=True,
        allow_map_write=True,
        allow_arm=True,
        allow_hand=True,
        allow_voice=True,
        allow_tour=True,
    )


def _gate(config: Optional[SafetyConfig] = None) -> SafetyGate:
    return SafetyGate(
        config=config if config is not None else _permissive(),
        clock=ManualClock(mono_ns=1_000_000_000),
    )


def _ctx(**overrides) -> SafetyContext:
    base = dict(command_id="req_test", caller="tester", description="unit test")
    base.update(overrides)
    return SafetyContext(**base)


def test_every_allow_flag_defaults_to_false() -> None:
    config = SafetyConfig()
    for flag in INTERLOCK_FOR_FLAG:
        assert getattr(config, flag) is False, "%s must default to False" % flag
    assert config.any_write_allowed is False


@pytest.mark.parametrize(
    "command_class,flag", GATED_CLASSES, ids=[str(k) for k, _ in GATED_CLASSES]
)
def test_a_default_gate_refuses_every_gated_command_class(
    command_class: CommandClass, flag: str
) -> None:
    gate = _gate(SafetyConfig())
    decision = gate.check(command_class, _ctx())
    assert decision.denied
    assert INTERLOCK_FOR_FLAG[flag] in decision.interlocks
    assert decision.required_flag == flag
    assert isinstance(decision.to_error(), SafetyInterlockError)


def test_read_only_is_always_allowed() -> None:
    gate = _gate(SafetyConfig())
    decision = gate.check(CommandClass.READ_ONLY, _ctx(estop_engaged=True, adapter_ready=False))
    assert decision.allowed


def test_navigation_is_denied_when_no_map_selected() -> None:
    gate = _gate()
    decision = gate.check(CommandClass.NAVIGATION, _ctx(map_not_selected=True))
    assert decision.denied
    assert Interlock.MAP_NOT_SELECTED in decision.interlocks


def test_navigation_is_denied_when_localization_not_initialized() -> None:
    gate = _gate()
    decision = gate.check(
        CommandClass.NAVIGATION, _ctx(map_not_selected=False, localization_not_initialized=True)
    )
    assert decision.denied
    assert Interlock.LOCALIZATION_NOT_INITIALIZED in decision.interlocks


def test_navigation_is_allowed_when_map_selected_and_localized() -> None:
    gate = _gate()
    decision = gate.check(
        CommandClass.NAVIGATION, _ctx(map_not_selected=False, localization_not_initialized=False)
    )
    assert decision.allowed


def test_tour_running_blocks_manual_motion_and_arm() -> None:
    gate = _gate()
    for klass in (CommandClass.MOTION, CommandClass.NAVIGATION, CommandClass.ARM, CommandClass.HAND):
        decision = gate.check(
            klass,
            _ctx(map_not_selected=False, localization_not_initialized=False, tour_running=True),
        )
        assert decision.denied, klass
        assert Interlock.TOUR_RUNNING in decision.interlocks


def test_middleware_interlocks_deny_motion() -> None:
    gate = _gate()
    decision = gate.check(
        CommandClass.NAVIGATION, _ctx(roscore_down=True, map_not_selected=False, localization_not_initialized=False)
    )
    assert Interlock.ROSCORE_DOWN in decision.interlocks
    decision = gate.check(
        CommandClass.NAVIGATION, _ctx(move_base_down=True, map_not_selected=False, localization_not_initialized=False)
    )
    assert Interlock.MOVE_BASE_DOWN in decision.interlocks
    decision = gate.check(
        CommandClass.MOTION, _ctx(velocity_bridge_down=True)
    )
    assert Interlock.VELOCITY_BRIDGE_DOWN in decision.interlocks


def test_a_denial_reports_every_active_interlock() -> None:
    gate = _gate()
    decision = gate.check(
        CommandClass.NAVIGATION,
        _ctx(
            map_not_selected=True,
            localization_not_initialized=True,
            move_base_down=True,
            roscore_down=True,
            dds_unreachable=True,
        ),
    )
    assert decision.denied
    for expected in (
        Interlock.ROSCORE_DOWN,
        Interlock.MOVE_BASE_DOWN,
        Interlock.DDS_UNREACHABLE,
        Interlock.MAP_NOT_SELECTED,
        Interlock.LOCALIZATION_NOT_INITIALIZED,
    ):
        assert expected in decision.interlocks, expected


def test_adapter_not_ready_is_503() -> None:
    gate = _gate()
    decision = gate.check(CommandClass.NAVIGATION, _ctx(adapter_ready=False))
    assert decision.denied
    assert decision.denial_kind == DenialKind.NOT_READY
    assert isinstance(decision.to_error(), NotReadyError)


def test_non_finite_speed_is_unsafe() -> None:
    gate = _gate()
    decision = gate.check(CommandClass.MOTION, _ctx(requested_linear_mps=float("nan")))
    assert decision.denied
    assert decision.denial_kind == DenialKind.UNSAFE
    assert isinstance(decision.to_error(), UnsafeRequestError)


def test_excessive_speed_is_clamped_not_rejected() -> None:
    gate = _gate(SafetyConfig(allow_motion=True, max_linear_speed_mps=1.0, max_angular_speed_radps=0.7))
    decision = gate.check(CommandClass.MOTION, _ctx(requested_linear_mps=5.0, requested_angular_radps=-4.0))
    assert decision.allowed
    assert decision.clamped
    assert decision.clamped_linear_mps == pytest.approx(1.0)
    assert decision.clamped_angular_radps == pytest.approx(-0.7)


def test_teleop_kinds_are_gated_by_allow_motion() -> None:
    assert command_class_for_action_kind(ActionKind.MOVE) == CommandClass.MOTION
    for kind in (ActionKind.MOVE_TO, ActionKind.ROTATE, ActionKind.ROTATE_TO):
        assert command_class_for_action_kind(kind) == CommandClass.NAVIGATION
