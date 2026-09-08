"""The real G1 adapter: import-clean and stubbed, never actually initialized."""

from __future__ import annotations

import dataclasses

import pytest

from g1_api.config import AppConfig, ModeConfig


def test_import_g1_api_never_imports_ros_or_dds() -> None:
    import sys

    import g1_api  # noqa: F401

    assert "rospy" not in sys.modules
    assert "unitree_sdk2py" not in sys.modules


def test_g1_adapter_module_imports_cleanly() -> None:
    from g1_api.adapters.g1.adapter import G1Adapter  # noqa: F401
    from g1_api.adapters.g1 import dds_bridge, launch_manager, navigation_sdk, ros_bridge  # noqa: F401


def test_real_adapter_refuses_to_start_without_interface() -> None:
    import asyncio

    from g1_api.adapters.g1.adapter import G1Adapter

    config = AppConfig(mode=ModeConfig(mode="real", network_interface=None))
    adapter = G1Adapter(config)
    with pytest.raises(Exception) as excinfo:
        asyncio.get_event_loop().run_until_complete(adapter.start())
    assert "network_interface" in str(excinfo.value) or "G1_NETWORK_INTERFACE" in str(excinfo.value)


def test_dds_bridge_requires_iface() -> None:
    from g1_api.adapters.g1.dds_bridge import DdsBridge

    with pytest.raises(RuntimeError):
        DdsBridge(None)


def test_build_adapter_real_requires_interface_at_start() -> None:
    import asyncio

    from g1_api.adapters import build_adapter

    config = AppConfig(mode=ModeConfig(mode="real", network_interface=None))
    adapter = build_adapter(config)
    assert adapter.name == "g1"
    with pytest.raises(Exception):
        asyncio.get_event_loop().run_until_complete(adapter.start())


def test_hand_adapter_requires_robot_ip() -> None:
    import asyncio

    from g1_api.adapters.g1.adapter import G1HandAdapter
    from g1_api.config import AppConfig, HandConfig

    hand = G1HandAdapter(AppConfig(hand=HandConfig(robot_ip=None)))
    with pytest.raises(Exception) as excinfo:
        asyncio.get_event_loop().run_until_complete(hand.send_command("7"))
    assert "robot_ip" in str(excinfo.value)


def test_mock_is_the_default_adapter() -> None:
    from g1_api.adapters import build_adapter

    adapter = build_adapter(AppConfig())
    assert adapter.name == "mock"
