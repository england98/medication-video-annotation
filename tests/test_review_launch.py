"""Review selection, independent ledgers and real process lifecycle on synthetic media."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from medication_video_annotation.demo import create_demo
from medication_video_annotation.io import file_hash
from medication_video_annotation.review_session import review_session
from medication_video_annotation.server import Workbench
from medication_video_annotation.timing import Run, create_run


def completed_source(root):
    path = create_run(root / "runs", "closed-batch", "TRIAL", completion_scope="DRAFT_EFFICIENCY")
    run = Run(path)
    run.start("selection", "SELECTION")
    run.transition("selection", "FINISH", result="COMPLETE")
    run.select_targets(["DEMO-02"])
    for stage in ("PREPARATION", "OBSERVATION_DRAFT", "VALIDATE_SAVE"):
        run.start(stage, stage, video_id="DEMO-02")
        run.transition(stage, "FINISH", result="COMPLETE")
    run.finish_run()
    return path


def test_selection_reuse_and_closed_source_is_immutable(tmp_path):
    root = tmp_path / "synthetic"
    create_demo(root)
    source = completed_source(root)
    before = {p: file_hash(p) for p in source.iterdir()}
    path, ids, selection = review_session(root, from_run=source)
    assert ids == ["DEMO-02"] and selection["mode"] == "batch"
    assert Run(path).meta["run_kind"] == "REVIEW"
    assert review_session(root, from_run=source)[0] == path
    all_path, ids, _ = review_session(root, all_videos=True)
    assert ids == ["DEMO-01", "DEMO-02"]
    selected, _, _ = review_session(root, ids=["DEMO-02", "DEMO-01"])
    assert review_session(root, ids=["DEMO-01", "DEMO-02"])[0] == selected
    assert all_path != path != selected
    bench = Workbench(root, path, ["DEMO-02"])
    data = bench.record("DEMO-02")
    actor = "synthetic-reviewer"
    assert bench.write("save", "DEMO-02", {**data, "actor_ref": actor})["status"] == "UNCHANGED"
    bench.timer("DEMO-02", dict(stage="HUMAN_REVIEW", actor_ref=actor, action="START"))
    result = bench.write(
        "review",
        "DEMO-02",
        dict(
            base_hash=data["base_hash"],
            actor_ref=actor,
            reviewed_ranges=[{"start_frame_index": 0, "end_frame_index": 15}],
        ),
    )
    assert result["record"]["label"]["coverage"]["annotation_status"] == "REVIEWED"
    bench.run.finish_run()
    assert review_session(root, from_run=source)[0] != path
    assert all(file_hash(p) == digest for p, digest in before.items())
    assert not (root / "exports").exists()
    count = len(list((root / "runs").iterdir()))
    for kwargs in (
        {"ids": []},
        {"ids": ["DEMO-01", "DEMO-01"]},
        {"ids": ["missing"]},
        {},
        {"all_videos": True, "ids": ["DEMO-01"]},
    ):
        with pytest.raises(ValueError):
            review_session(root, **kwargs)
    assert len(list((root / "runs").iterdir())) == count


def test_three_modes_stop_restart_and_http_save(tmp_path):
    root = tmp_path / "한글 공백 합성"
    create_demo(root)
    source = completed_source(root)
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    prefix = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "medication_video_annotation",
        "--data-root",
        str(root),
    ]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"http://127.0.0.1:{port}"

    def get(route):
        with urlopen(address + route, timeout=2) as response:
            return json.load(response)

    def post(route, value, token):
        request = Request(
            address + route,
            json.dumps(value).encode(),
            {"Content-Type": "application/json", "X-Workbench-Token": token},
        )
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    prior_run = None
    modes = [
        (["--all"], ["DEMO-01", "DEMO-02"], "all"),
        (["--ids", "DEMO-01"], ["DEMO-01"], "selected"),
        (["--from-run", str(source)], ["DEMO-02"], "batch"),
        (["--from-run", str(source)], ["DEMO-02"], "batch"),
    ]
    for n, (arguments, expected, mode) in enumerate(modes):
        server = subprocess.Popen(
            [*prefix, "review", *arguments, "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    info = get("/api/index")
                    break
                except (URLError, ConnectionError):
                    assert time.monotonic() < deadline and server.poll() is None
                    time.sleep(0.05)
            assert info["ids"] == expected and info["selection_mode"] == mode
            sid, token = expected[0], info["token"]
            data = get(f"/api/record/{sid}")
            assert data["revision_id"] and data["frames"]
            timer = dict(stage="HUMAN_REVIEW", actor_ref="synthetic-http", action="START")
            if n == 3:
                assert info["run_id"] == prior_run
                assert data["timers"]["HUMAN_REVIEW"]["state"] == "INTERRUPTED"
                timer["action"] = "RESUME"
            post(f"/api/timer/{sid}", timer, token)
            assert (
                post(f"/api/save/{sid}", {**data, "actor_ref": "synthetic-http"}, token)["status"]
                == "UNCHANGED"
            )
            if n == 3:
                data["record"]["analysis"]["analyst"] = "synthetic-http-correction"
                changed = post(f"/api/save/{sid}", {**data, "actor_ref": "synthetic-http"}, token)
                assert changed["revision_id"] == "revision-000001.json"
                assert changed["record"]["analysis"]["status"] == "DRAFT"
                assert get(f"/api/record/{sid}")["revision_id"] == changed["revision_id"]
            blocked = subprocess.run(
                [*prefix, "review", "--all", "--port", str(port + 1)],
                capture_output=True,
                encoding="utf-8",
                env=env,
                timeout=10,
            )
            assert blocked.returncode != 0 and "Writer lock exists" in blocked.stderr
            stopped = subprocess.run(
                [*prefix, "stop", "--port", str(port)],
                capture_output=True,
                encoding="utf-8",
                env=env,
                timeout=15,
            )
            assert stopped.returncode == 0, stopped.stderr
            assert json.loads(stopped.stdout)["status"] == "STOPPED"
            assert server.wait(timeout=5) == 0
            assert not (root / "service/.writer.lock").exists()
            with pytest.raises((URLError, ConnectionError)):
                get("/api/index")
            prior_run = info["run_id"]
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)
    assert not (root / "exports").exists()
