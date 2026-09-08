"""Planner-feedback nav mode: config surface, launch overlay wrapping, and the
actionlib status interpretation the AeroMaze integration relies on."""

from __future__ import annotations

import pytest

from g1_api.config import AppConfig, ConfigError, ModeConfig, load_config
from g1_api.adapters.g1.launch_manager import LaunchManager
from g1_api.adapters.g1.ros_bridge import interpret_planner_status


def test_nav_feedback_defaults_to_tf() -> None:
    assert AppConfig().mode.nav_feedback == "tf"
    assert AppConfig().mode.nav_launch_setup is None


def test_nav_feedback_env_override() -> None:
    config = load_config(
        env={
            "G1_API_MODE_NAV_FEEDBACK": "planner",
            "G1_API_MODE_NAV_LAUNCH_SETUP": "/opt/aero/devel/setup.bash",
        },
        warn=False,
    )
    assert config.mode.nav_feedback == "planner"
    assert config.mode.nav_launch_setup == "/opt/aero/devel/setup.bash"


def test_nav_feedback_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError):
        ModeConfig(nav_feedback="magic")


def test_launch_command_unchanged_without_setup_file() -> None:
    mgr = LaunchManager("/deploy/nav.launch", args={"grid_yaml": "/maps/a.yaml"})
    assert mgr._command() == ["roslaunch", "/deploy/nav.launch", "grid_yaml:=/maps/a.yaml"]


def test_launch_command_wraps_with_setup_file() -> None:
    mgr = LaunchManager(
        "/deploy/nav_aero.launch",
        args={"grid_yaml": "/maps/a.yaml"},
        setup_file="/opt/aero/devel/setup.bash",
    )
    cmd = mgr._command()
    assert cmd[:2] == ["bash", "-c"]
    # exec keeps roslaunch as the direct child so stop()/_kill_strays still hit
    assert "exec roslaunch" in cmd[2]
    assert "source /opt/aero/devel/setup.bash" in cmd[2]
    assert "grid_yaml:=/maps/a.yaml" in cmd[2]


def test_interpret_planner_status_mapping() -> None:
    assert interpret_planner_status(0) is None      # PENDING
    assert interpret_planner_status(1) is None      # ACTIVE
    assert interpret_planner_status(3) == "success" # SUCCEEDED
    assert interpret_planner_status(2) == "failed"  # PREEMPTED (external)
    for terminal_failure in (4, 5, 8, 9):           # ABORTED/REJECTED/RECALLED/LOST
        assert interpret_planner_status(terminal_failure) == "failed"
