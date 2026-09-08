"""Configuration defaults and env-var overrides."""

from __future__ import annotations

from g1_api.config import AppConfig, RobotMode, SafetyConfig, load_config


def test_default_mode_is_mock() -> None:
    config = AppConfig()
    assert config.mode.mode == RobotMode.MOCK
    assert config.mode.is_mock


def test_default_port_is_1448() -> None:
    assert AppConfig().server.port == 1448


def test_default_network_interface_is_none() -> None:
    assert AppConfig().mode.network_interface is None


def test_env_var_override() -> None:
    config = load_config(
        env={"G1_API_MODE": "real", "G1_API_SAFETY_ALLOW_NAVIGATION": "true"}, warn=False
    )
    assert config.mode.mode == RobotMode.REAL
    assert config.safety.allow_navigation is True
    assert config.safety.allow_motion is False


def test_every_allow_flag_defaults_false() -> None:
    safety = SafetyConfig()
    for name in (
        "allow_motion", "allow_navigation", "allow_map_write",
        "allow_arm", "allow_hand", "allow_voice", "allow_tour",
    ):
        assert getattr(safety, name) is False


def test_move_duration_default_cap_is_3000() -> None:
    assert SafetyConfig().max_move_duration_ms == 3000


def test_deadzones() -> None:
    s = SafetyConfig()
    assert s.deadzone_vx == 0.2
    assert s.deadzone_vy == 0.2
    assert s.deadzone_omega == 0.3
