import json
import os
import subprocess
import sys
from pathlib import Path

from medication_video_annotation.timing import create_run


def test_session_cli_keeps_one_clock_and_accepts_transitions(tmp_path):
    folder = create_run(tmp_path, "CLI-TEST", "SETUP")
    requests = [
        {"command": "start", "task_id": "work", "stage": "SETUP_IMPLEMENTATION"},
        {"command": "transition", "task_id": "work", "action": "PAUSE", "reason": "USER_WAIT"},
        {"command": "transition", "task_id": "work", "action": "RESUME"},
        {"command": "transition", "task_id": "work", "action": "FINISH", "result": "COMPLETE"},
        {"command": "finish_run"},
    ]
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "medication_video_annotation.timing",
            "session",
            "--run",
            str(folder),
        ],
        input="\n".join(json.dumps(v) for v in requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 5 and all(r["ok"] for r in responses), responses
    assert responses[-1]["value"]["result"] == "COMPLETE"
    assert responses[1]["value"]["segment"]["seconds"] >= 0
