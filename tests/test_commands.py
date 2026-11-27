"""Process-level standalone commands with synthetic data only."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from medication_video_annotation.demo import create_demo
from medication_video_annotation.io import atomic_json, read


def test_first_save_checkpoint_serve_stop_and_restart(tmp_path):
    root = tmp_path / "synthetic"
    create_demo(root)
    run = root / "runs/synthetic-workbench"
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

    def command(*args):
        result = subprocess.run(
            [*prefix, *args], capture_output=True, encoding="utf-8", env=env, timeout=20
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    original = root / "records/DEMO-02/revision-000000.json"
    draft = read(original)
    draft["revision_history"] = []
    original.unlink()
    draft_path = root / "draft-input.json"
    atomic_json(draft_path, draft)
    common = [
        "--run",
        str(run),
        "--id",
        "DEMO-02",
        "--input",
        str(draft_path),
        "--actor",
        "synthetic-cli",
    ]
    command("checkpoint", *common, "--cursor", "4")
    assert not original.exists()
    saved = command("save", *common)
    assert saved["revision_id"] == "revision-000000.json"
    atomic_json(draft_path, saved["record"])
    assert command("save", *common, "--base-hash", saved["base_hash"])["status"] == "UNCHANGED"
    assert not (root / "exports").exists()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    for _ in range(2):
        server = subprocess.Popen(
            [*prefix, "serve", "--run", str(run), "--ids", "DEMO-02", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/index", timeout=1) as response:
                        assert json.load(response)["ids"] == ["DEMO-02"]
                    break
                except (URLError, ConnectionError):
                    assert time.monotonic() < deadline and server.poll() is None
                    time.sleep(0.05)
            blocked = subprocess.run(
                [*prefix, "save", *common, "--base-hash", saved["base_hash"]],
                capture_output=True,
                encoding="utf-8",
                env=env,
            )
            assert blocked.returncode != 0 and "Stop this data root" in blocked.stderr
            assert command("stop", "--port", str(port))["status"] == "STOPPED"
            assert server.wait(timeout=5) == 0
            assert not (root / "service/.writer.lock").exists()
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)
