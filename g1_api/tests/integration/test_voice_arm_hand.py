"""TTS, arm and hand endpoints."""

from __future__ import annotations

from typing import Any

import pytest


def test_tts_returns_estimate(client: Any, mock_world: Any) -> None:
    text = "你好，我是深蓝机器人"
    resp = client.post("/api/core/voice/v1/tts", json={"text": text, "speaker_id": 0, "wait": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["est_duration_s"] == pytest.approx(len(text) * 0.195)
    assert mock_world.tts_log[-1] == text


def test_tts_wait_sleeps(client: Any) -> None:
    import time

    start = time.monotonic()
    resp = client.post("/api/core/voice/v1/tts", json={"text": "你好世界", "wait": True})
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    assert elapsed >= 0.5


def test_volume_and_led(client: Any, mock_world: Any) -> None:
    resp = client.put("/api/core/voice/v1/volume", json={"volume": 42})
    assert resp.status_code == 200
    assert resp.json()["volume"] == 42
    assert client.get("/api/core/voice/v1/volume").json()["volume"] == 42

    resp = client.put("/api/core/voice/v1/led", json={"r": 255, "g": 0, "b": 0})
    assert resp.status_code == 200
    assert mock_world.led == (255, 0, 0)


def test_arm_action_list_and_execute(client: Any, mock_world: Any) -> None:
    resp = client.get("/api/core/arm/v1/action-list")
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    ids = [a["id"] for a in actions]
    assert 99 in ids and 26 in ids

    resp = client.post("/api/core/arm/v1/actions", json={"action_id": 26})
    assert resp.status_code == 200
    assert 26 in mock_world.arm_log
    assert 99 in mock_world.arm_log  # release arm after every action


def test_hand_command(client: Any, mock_world: Any) -> None:
    resp = client.post("/api/core/hand/v1/command", json={"cmd": "7"})
    assert resp.status_code == 200
    assert resp.json()["cmd"] == "7"
    assert "7" in mock_world.hand_log


def test_hand_bad_command_is_400(client: Any) -> None:
    resp = client.post("/api/core/hand/v1/command", json={"cmd": "99"})
    assert resp.status_code == 400


def test_voice_denied_without_flag(locked_config: Any) -> None:
    from tests.support.harness import make_client

    with make_client(locked_config) as client:
        resp = client.post("/api/core/voice/v1/tts", json={"text": "hi"})
        assert resp.status_code == 409, resp.text
        assert "voice_not_allowed" in resp.json().get("details", {}).get("active_interlocks", [])
