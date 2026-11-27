"""Synthetic-only integration regression, adapted from the ONC-110 pilot tests."""

import copy
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from medication_video_annotation.demo import create_demo
from medication_video_annotation.io import file_hash, read
from medication_video_annotation.media import prepare_selection, verify_prepared
from medication_video_annotation.records import load_index
from medication_video_annotation.server import Workbench, make_server
from medication_video_annotation.timing import Run
from medication_video_annotation.workflow import read_release


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "한글 공백 합성 시험"
    create_demo(root)
    run_path = root / "runs/synthetic-workbench"
    return root, run_path, ["DEMO-01", "DEMO-02"]


def save_value(data, record=None):
    return {
        "record": record or data["record"],
        "base_hash": data["base_hash"],
        "actor_ref": "synthetic-reviewer",
    }


def timer(bench, action, stage="HUMAN_REVIEW", **kwargs):
    return bench.write(
        "timer",
        "DEMO-01",
        dict(action=action, stage=stage, actor_ref="synthetic-reviewer", **kwargs),
    )


def test_prepare_idempotent_nonzero_pts_and_partial_resume(demo, monkeypatch):
    root, run_path, _ = demo
    items = load_index(root)
    paths = [p for p in root.rglob("*") if p.is_file() and p.suffix in {".png", ".mp4"}]
    before = {p: (file_hash(p), p.stat().st_mtime_ns) for p in paths}
    result = prepare_selection(root, items, Run(run_path))
    assert all(r["status"] == "REUSED" for r in result)
    assert all((file_hash(p), p.stat().st_mtime_ns) == info for p, info in before.items())
    mapping = verify_prepared(root, items[0])
    assert mapping["first_source_ms"] == 1400
    assert mapping["frames"][0]["player_elapsed_ms"] == 0
    assert read(root / "cache/DEMO-01/playback.json")["video_packets_unchanged"]
    interrupted = Run(run_path)
    interrupted.start(
        "frames-DEMO-01",
        "PREPARATION",
        video_id="DEMO-01",
        activity_kind="TOOL_EXECUTION",
        retry_reason="Synthetic post-commit crash",
    )
    assert prepare_selection(root, [items[0]], Run(run_path))[0]["status"] == "REUSED"
    attempt = Run(run_path).summary()["attempts"][-1]
    assert attempt["status"] == "COMPLETE" and attempt["work_seconds"] is None
    # Crash after chunks.json but before frame-map.json: keep completed PNGs/chunks.
    (root / items[0]["frame_map_ref"]).unlink()
    assert prepare_selection(root, [items[0]], Run(run_path))[0]["status"] == "PREPARED"
    assert all((file_hash(p), p.stat().st_mtime_ns) == info for p, info in before.items())
    assert not (root / "exports").exists()
    assert len(list((root / "records").rglob("revision-*.json"))) == 2
    image = root / mapping["frames"][0]["image_path"]
    image.write_bytes(b"corrupted synthetic frame")
    with pytest.raises(ValueError, match="hash changed"):
        prepare_selection(root, items, Run(run_path))
    assert image.read_bytes() == b"corrupted synthetic frame"


def test_save_review_correction_checkpoint_export_and_fixed_release(demo):
    root, run_path, ids = demo
    bench = Workbench(root, run_path, ids)
    data = bench.record(ids[0])
    initial_file = root / "records/DEMO-01/revision-000000.json"
    initial_hash = file_hash(initial_file)
    assert bench.write("save", ids[0], save_value(data))["status"] == "UNCHANGED"
    ck = bench.write("checkpoint", ids[0], {**save_value(data), "cursor": 7})
    assert bench.record(ids[0])["checkpoint"]["cursor"] == 7
    with pytest.raises(ValueError, match="Checkpoint changed"):
        bench.write("checkpoint", ids[0], {**save_value(data), "cursor": 8})
    review = {
        "base_hash": data["base_hash"],
        "actor_ref": "synthetic-reviewer",
        "reviewed_ranges": [{"start_frame_index": 0, "end_frame_index": 15}],
        "exact_event_ids": ["SYNTH-E03"],
    }
    with pytest.raises(ValueError, match="timer"):
        bench.write("review", ids[0], review)
    timer(bench, "START")
    timer(bench, "PAUSE", reason="USER_WAIT")
    timer(bench, "RESUME")
    reviewed = bench.write("review", ids[0], review)
    assert reviewed["revision_id"] == "revision-000001.json"
    assert reviewed["record"]["label"]["events"][0]["interval"]["boundary_status"] == "EXACT"
    assert reviewed["record"]["label"]["final_ground_truth"] is False
    assert bench.record(ids[0])["checkpoint_stale"]
    assert file_hash(initial_file) == initial_hash
    # Repeating an already-confirmed review creates no duplicate revision.
    timer(bench, "START")
    same = bench.write("review", ids[0], {**review, "base_hash": reviewed["base_hash"]})
    assert same["status"] == "UNCHANGED"
    changed = copy.deepcopy(reviewed["record"])
    changed["analysis"]["categories"]["behavior"]["observations"][0]["note"] += " corrected"
    corrected = bench.write("save", ids[0], save_value(reviewed, changed))
    assert corrected["revision_id"] == "revision-000002.json"
    assert corrected["record"]["analysis"]["status"] == "DRAFT"
    assert corrected["record"]["label"]["coverage"]["annotation_status"] == "DRAFT"
    event = corrected["record"]["label"]["events"][0]
    assert event["reference_verification"] is None
    assert event["interval"]["boundary_status"] == "APPROXIMATE"
    assert bench.timer_state(ids[0], "HUMAN_REVIEW")["state"] == "CANCELLED"
    assert not (root / "exports").exists()
    timer(bench, "START")
    final = bench.write("review", ids[0], {**review, "base_hash": corrected["base_hash"]})
    export = {
        "revision_id": final["revision_id"],
        "base_hash": final["base_hash"],
        "actor_ref": "synthetic-reviewer",
    }
    with pytest.raises(ValueError, match="Explicit"):
        bench.write("export", ids[0], export)
    assert not (root / "exports").exists()
    exported = bench.write("export", ids[0], {**export, "request": "USER_REVIEW_BUTTON"})
    assert len(list(Path(exported["path"]).iterdir())) == 5
    assert read(Path(exported["path"]) / "record.json") == final["record"]
    assert (
        bench.write("export", ids[0], {**export, "request": "USER_REVIEW_BUTTON"})["status"]
        == "REUSED"
    )
    item = bench.items[ids[0]]
    fixed = {
        "format": "onc110-release-v0.1",
        "entries": [
            {
                "video_id": ids[0],
                "source_path": item["source_path"],
                "source_sha256": item["source_sha256"],
                "frame_map_ref": item["frame_map_ref"],
                "frame_map_sha256": file_hash(root / item["frame_map_ref"]),
                "revision_ref": "records/DEMO-01/revision-000000.json",
                "revision_file_sha256": initial_hash,
            }
        ],
    }
    assert read_release(root, fixed)[0] == data["record"]
    fixed["entries"][0]["revision_file_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        read_release(root, fixed)
    summary = bench.run.summary()
    assert any(
        s["kind"] == "WAIT" and s["reason"] == "USER_WAIT"
        for a in summary["attempts"]
        for s in a["segments"]
    )
    assert ck["checkpoint"]["base_hash"] == data["base_hash"]


def test_restart_timers_blank_get_forged_review_and_stale_save(demo):
    root, run_path, ids = demo
    bench = Workbench(root, run_path, ids)
    timer(bench, "START")
    restarted = Workbench(root, run_path, ids)
    assert restarted.timer_state(ids[0], "HUMAN_REVIEW")["state"] == "NEEDS_RECOVERY"
    with pytest.raises(ValueError, match="RECOVER"):
        timer(restarted, "PAUSE", reason="USER_WAIT")
    timer(restarted, "RECOVER", reason="Synthetic restart")
    timer(restarted, "RESUME")
    timer(restarted, "CANCEL")
    attempt = restarted.run.summary()["attempts"][-1]
    assert attempt["work_seconds"] is None and attempt["measurement_status"] == "PARTIALLY_MEASURED"
    assert any(s["seconds"] is None for s in attempt["segments"])
    data = restarted.record(ids[0])
    fake = copy.deepcopy(data["record"])
    fake["analysis"]["categories"]["people"]["status"] = "REVIEWED"
    with pytest.raises(ValueError, match="review action"):
        restarted.write("save", ids[0], save_value(data, fake))
    fake = copy.deepcopy(data["record"])
    fake["analysis"]["categories"]["people"]["observations"][0]["note"] = "new synthetic note"
    restarted.write("save", ids[0], save_value(data, fake))
    with pytest.raises(ValueError, match="changed"):
        restarted.write("save", ids[0], save_value(data))
    # A newly prepared clip has only an in-memory blank template on GET.
    (root / "records/DEMO-02/revision-000000.json").unlink()
    blank = restarted.record(ids[1])
    assert blank["revision_id"] is None
    with pytest.raises(ValueError):
        restarted.write("save", ids[1], save_value(blank))
    assert not list((root / "records/DEMO-02").glob("revision-*.json"))


@pytest.mark.parametrize("error_type", [ConnectionResetError, ConnectionAbortedError, BrokenPipeError])
def test_cancelled_media_does_not_send_second_response(demo, error_type):
    root, run_path, ids = demo
    server = make_server(root, run_path, ids, 0)
    try:
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.path = "/api/frame/DEMO-01/0"
        handler.allowed = lambda: True
        handler.close_connection = False

        def cancelled_media(path):
            assert path.is_file()
            raise error_type("Browser cancelled an in-flight response")

        handler.media = cancelled_media
        handler.respond = lambda *a, **k: pytest.fail("Second response on a closed connection")
        handler.do_GET()
        assert handler.close_connection
    finally:
        server.server_close()


def test_http_range_conflict_local_origin_and_shutdown(demo):
    root, run_path, ids = demo
    server = make_server(root, run_path, ids, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_port}"

    def fetch(path, value=None, **headers):
        req = Request(
            address + path,
            data=None if value is None else json.dumps(value).encode(),
            headers=headers,
        )
        return urlopen(req, timeout=5)

    try:
        with fetch("/api/index") as response:
            index = json.load(response)
        assert index["ids"] == ids
        with fetch("/api/video/DEMO-01", Range="bytes=0-31") as response:
            assert response.status == 206 and len(response.read()) == 32
        with pytest.raises(HTTPError) as exc:
            fetch("/api/video/DEMO-01", Range="bytes=99999999-")
        assert exc.value.code == 416
        with pytest.raises(HTTPError) as exc:
            fetch("/api/index", Origin="https://unrelated.invalid")
        assert exc.value.code == 403
        with pytest.raises(OSError):
            make_server(root, run_path, ids, server.server_port)
        with fetch("/api/record/DEMO-01") as response:
            data = json.load(response)
        with pytest.raises(HTTPError) as exc:
            fetch("/api/save/DEMO-01", save_value(data), **{"Content-Type": "application/json"})
        assert exc.value.code == 403
        headers = {"Content-Type": "application/json", "X-Workbench-Token": index["token"]}
        with fetch("/api/save/DEMO-01", save_value(data), **headers) as response:
            assert json.load(response)["status"] == "UNCHANGED"
        with fetch(
            "/api/timer/DEMO-01",
            {"action": "START", "stage": "HUMAN_REVIEW", "actor_ref": "synthetic-reviewer"},
            **headers,
        ) as response:
            assert json.load(response)["state"] == "ACTIVE"
        with fetch("/api/control/stop", {}, **headers) as response:
            assert json.load(response)["status"] == "STOPPING"
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()
    state = Workbench(root, run_path, ids).timer_state("DEMO-01", "HUMAN_REVIEW")
    assert state["state"] == "INTERRUPTED" and state["known_work_seconds"] >= 0


def test_index_conflict_fails_before_new_frame_output(demo):
    root, run_path, _ = demo
    selected = read(root / "selection.json")
    selected[0]["record_dir"] = "records/DIFFERENT"
    original_map = root / selected[0]["frame_map_ref"]
    original_map.unlink()
    with pytest.raises(ValueError, match="identity changed"):
        prepare_selection(root, selected, Run(run_path))
    assert not original_map.exists()


def test_config_rejects_silent_policy_changes(demo, tmp_path):
    import os
    import subprocess
    import sys

    config = tmp_path / "config.toml"
    config.write_text(
        'config_version = "0.1"\n[observation]\nchunk_frames = 20\n', encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "medication_video_annotation",
            "--config",
            str(config),
            "--data-root",
            str(demo[0]),
            "validate",
            "--ids",
            "DEMO-01",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert result.returncode != 0 and "Unsupported policy override" in result.stderr
