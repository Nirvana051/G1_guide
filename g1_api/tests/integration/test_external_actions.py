"""External (replay) action library: upload/list/delete, execute via a
monkeypatched transport, the external_motion_running interlock, and the tour
integration (replay actions resolve the library by id)."""

from __future__ import annotations

import os
import shutil
import struct
from typing import Any, Dict, Iterator, List, Tuple

import pytest

import g1_api.utils.external_executor as transport
from g1_api.core.safety import CommandClass, SafetyContext
from g1_api.models.external_action import parse_npy_header
from g1_api.models.state import Interlock
from tests.support.harness import PROJECT_ROOT, upload_select_relocalize, wait_until

BASE = "/api/core/motion/v1/external-actions"


@pytest.fixture(autouse=True, scope="module")
def _clean_library() -> Iterator[None]:
    path = os.path.join(PROJECT_ROOT, ".test_external_actions")
    shutil.rmtree(path, ignore_errors=True)
    yield
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def stub_executor(core: Any, monkeypatch: Any) -> Iterator[List[Tuple[str, Dict[str, Any], float]]]:
    """Point the config at a fake URL and capture post_json calls."""
    calls: List[Tuple[str, Dict[str, Any], float]] = []

    def fake_post(url: str, payload: Dict[str, Any], timeout_s: float = 15.0) -> int:
        calls.append((url, payload, timeout_s))
        return 200

    monkeypatch.setattr(transport, "post_json", fake_post)
    previous = core.config.tour.external_executor_url
    object.__setattr__(core.config.tour, "external_executor_url", "http://stub:9100/execute")
    yield calls
    object.__setattr__(core.config.tour, "external_executor_url", previous)


def make_npy(frames: int = 60, dims: int = 14) -> bytes:
    """Hand-craft a v1.0 .npy of float64 zeros -- no numpy dependency."""
    header = ("{'descr': '<f8', 'fortran_order': False, 'shape': (%d, %d), }"
              % (frames, dims))
    pad = 64 - ((10 + len(header) + 1) % 64)
    header = header + " " * pad + "\n"
    return (b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header))
            + header.encode("latin1") + b"\x00" * (8 * frames * dims))


def test_npy_header_parser() -> None:
    shape, dtype, fortran = parse_npy_header(make_npy(30, 16))
    assert shape == (30, 16) and dtype == "<f8" and fortran is False
    with pytest.raises(ValueError):
        parse_npy_header(b"not an npy at all")


def test_upload_list_meta_delete_roundtrip(client: Any) -> None:
    resp = client.post(BASE + "?name=wave hello&frequency=30&velocity_limit=10",
                       content=make_npy(90, 14),
                       headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 200, resp.text
    meta = resp.json()
    assert meta["action_id"] == "wave-hello"
    assert meta["frames"] == 90 and meta["dims"] == 14
    assert meta["duration_s"] == 3.0  # 90 frames @ 30 Hz

    listing = client.get(BASE).json()["actions"]
    assert [a["action_id"] for a in listing] == ["wave-hello"]
    assert client.get(BASE + "/wave-hello").json()["frequency_hz"] == 30.0
    assert client.get(BASE + "/wave-hello/file").content == make_npy(90, 14)

    assert client.delete(BASE + "/wave-hello").status_code == 200
    assert client.get(BASE + "/wave-hello").status_code == 404
    assert client.get(BASE).json()["actions"] == []


def test_upload_rejects_bad_shape_and_junk(client: Any) -> None:
    resp = client.post(BASE + "?name=bad", content=make_npy(10, 7),
                       headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 400
    resp = client.post(BASE + "?name=junk", content=b"garbage",
                       headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 400


def test_execute_posts_contract_to_executor(client: Any, core: Any, stub_executor: Any) -> None:
    client.post(BASE + "?name=demo&frequency=30", content=make_npy(30, 14),
                headers={"Content-Type": "application/octet-stream"})
    resp = client.post(BASE + "/demo/:execute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "finished"
    assert len(stub_executor) == 1
    url, payload, timeout_s = stub_executor[0]
    action = payload["action"]
    assert action["type"] == "replay" and action["id"] == "demo"
    assert action["file"].endswith("demo.npy") and os.path.isabs(action["file"])
    assert action["frequency"] == 30.0 and action["frames"] == 30
    # timeout follows duration (1 s) + margin (30 s default)
    assert timeout_s == pytest.approx(31.0)
    # the interlock is released after the run
    assert core.external_motion_running is False
    client.delete(BASE + "/demo")


def test_execute_without_executor_url_is_409(client: Any) -> None:
    client.post(BASE + "?name=lonely", content=make_npy(30, 14),
                headers={"Content-Type": "application/octet-stream"})
    resp = client.post(BASE + "/lonely/:execute")
    assert resp.status_code == 409
    assert "external_executor_url" in resp.text
    client.delete(BASE + "/lonely")


def test_execute_unknown_id_is_404(client: Any) -> None:
    assert client.post(BASE + "/no-such/:execute").status_code == 404


def test_external_motion_interlock_blocks_arm_and_navigation(core: Any) -> None:
    ctx = SafetyContext(external_motion_running=True, map_not_selected=False,
                        localization_not_initialized=False)
    for klass in (CommandClass.ARM, CommandClass.NAVIGATION, CommandClass.TOUR):
        decision = core.safety_gate.check(klass, ctx)
        assert decision.denied
        assert Interlock.EXTERNAL_MOTION_RUNNING in decision.interlocks
    # MOTION (teleop escape hatch) is not blocked by this interlock
    decision = core.safety_gate.check(CommandClass.MOTION, ctx)
    assert Interlock.EXTERNAL_MOTION_RUNNING not in decision.interlocks


def test_tour_resolves_type_as_library_id(client: Any, core: Any, stub_executor: Any) -> None:
    """The map editor's free-form field stores the id in `type`
    ({"executor":"external","type":"e1"}) -- that shape must resolve too."""
    client.post(BASE + "?name=e1&frequency=30", content=make_npy(60, 14),
                headers={"Content-Type": "application/octet-stream"})
    upload_select_relocalize(client, "typeid-tour")
    client.put(
        "/api/core/slam/v1/maps/typeid-tour/tour-points",
        json={"points": [
            {"name": "T1", "x": 0.3, "y": 0.0, "yaw": 0.0, "reach_threshold": 0.2,
             "actions": [{"executor": "external", "type": "e1"}],
             "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    assert client.post("/api/tour/v1/tours/:start").status_code == 200
    wait_until(
        lambda: client.get("/api/tour/v1/tours/:current").json()["status"]
        in ("finished", "failed"),
        description="type-as-id tour to end", timeout_s=30.0,
    )
    assert client.get("/api/tour/v1/tours/:current").json()["status"] == "finished"
    assert len(stub_executor) == 1
    action = stub_executor[0][1]["action"]
    assert action["type"] == "replay" and action["id"] == "e1"
    assert action["file"].endswith("e1.npy")
    client.delete(BASE + "/e1")


def test_consecutive_replays_batch_into_one_sequence(client: Any, core: Any, stub_executor: Any) -> None:
    """Two back-to-back external replays in one point must go out as a SINGLE
    replay_sequence job (one arm_sdk session, no hand-back in between)."""
    client.post(BASE + "?name=left&frequency=30", content=make_npy(30, 14),
                headers={"Content-Type": "application/octet-stream"})
    client.post(BASE + "?name=right&frequency=30", content=make_npy(60, 14),
                headers={"Content-Type": "application/octet-stream"})
    upload_select_relocalize(client, "seq-tour")
    client.put(
        "/api/core/slam/v1/maps/seq-tour/tour-points",
        json={"points": [
            {"name": "S1", "x": 0.3, "y": 0.0, "yaw": 0.0, "reach_threshold": 0.2,
             "actions": [{"executor": "external", "type": "replay", "id": "left"},
                         {"executor": "external", "type": "replay", "id": "right"}],
             "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    assert client.post("/api/tour/v1/tours/:start").status_code == 200
    wait_until(
        lambda: client.get("/api/tour/v1/tours/:current").json()["status"]
        in ("finished", "failed"),
        description="sequence tour to end", timeout_s=30.0,
    )
    assert client.get("/api/tour/v1/tours/:current").json()["status"] == "finished"
    assert len(stub_executor) == 1                      # ONE call, not two
    _, payload, timeout_s = stub_executor[0]
    action = payload["action"]
    assert action["type"] == "replay_sequence"
    assert [it["id"] for it in action["items"]] == ["left", "right"]
    assert action["duration_s"] == pytest.approx(3.0)   # 1s + 2s
    assert timeout_s == pytest.approx(33.0)             # sum + 30s margin
    client.delete(BASE + "/left"); client.delete(BASE + "/right")


def test_tour_replay_action_resolves_library(client: Any, core: Any, stub_executor: Any) -> None:
    client.post(BASE + "?name=bow&frequency=30", content=make_npy(30, 14),
                headers={"Content-Type": "application/octet-stream"})
    upload_select_relocalize(client, "replay-tour")
    client.put(
        "/api/core/slam/v1/maps/replay-tour/tour-points",
        json={"points": [
            {"name": "R1", "x": 0.3, "y": 0.0, "yaw": 0.0, "reach_threshold": 0.2,
             "actions": [{"executor": "external", "type": "replay", "id": "bow"}],
             "tts_text": "", "dwell_s": 0.0},
        ]},
    )
    assert client.post("/api/tour/v1/tours/:start").status_code == 200
    wait_until(
        lambda: client.get("/api/tour/v1/tours/:current").json()["status"]
        in ("finished", "failed"),
        description="replay tour to end", timeout_s=30.0,
    )
    final = client.get("/api/tour/v1/tours/:current").json()
    assert final["status"] == "finished", final
    assert len(stub_executor) == 1
    _, payload, timeout_s = stub_executor[0]
    action = payload["action"]
    assert action["id"] == "bow" and action["file"].endswith("bow.npy")
    assert payload["point"]["name"] == "R1"
    assert timeout_s == pytest.approx(31.0)
    assert core.external_motion_running is False
    client.delete(BASE + "/bow")
