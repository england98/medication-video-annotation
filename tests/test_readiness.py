"""Readiness checks reuse existing synthetic assets; never generate/extract video."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from medication_video_annotation.authoring import AuthorSession
from medication_video_annotation.io import file_hash, read
from medication_video_annotation.media import prepare_selection
from medication_video_annotation.operations import operation
from medication_video_annotation.records import load_index
from medication_video_annotation.timing import Run, create_run

PROJECT = Path(__file__).resolve().parents[1]
DEMO = PROJECT / "local/synthetic-demo"
SID = "DEMO-02"


def test_operation_preserves_empty_exception_and_retry(tmp_path):
    run = Run(create_run(tmp_path, "empty-error", "SETUP"))
    with pytest.raises(AssertionError):
        with operation(run, "check", "SETUP_VALIDATION"):
            raise AssertionError()
    row = run.summary()["attempts"][0]
    assert row["status"] == "FAIL" and row["reason"] == "AssertionError"
    assert row["work_seconds"] is not None
    with operation(run, "check", "SETUP_VALIDATION"):
        pass
    run.finish_run()
    assert [a["status"] for a in run.summary()["attempts"]] == ["FAIL", "COMPLETE"]


@pytest.fixture
def existing(tmp_path):
    if not (DEMO / "records/DEMO-02/revision-000000.json").exists():
        pytest.skip("Existing step-3 synthetic demo required; do not generate replacements")
    # Include all original demo files in the immutability guard, but only copy
    # prepared assets and one unreviewed draft into this isolated test root.
    before = {p: (file_hash(p), p.stat().st_mtime_ns) for p in DEMO.rglob("*") if p.is_file()}
    root = tmp_path / "synthetic-copy"
    for folder in ("data", "cache", "manifests"):
        shutil.copytree(DEMO / folder, root / folder)
    records = root / "records" / SID
    records.mkdir(parents=True)
    shutil.copy2(DEMO / "records" / SID / "revision-000000.json", records)
    run_path = create_run(
        root / "runs", "readiness", "TRIAL", video_ids=[SID], completion_scope="DRAFT_EFFICIENCY"
    )
    yield root, run_path
    assert {
        p: (file_hash(p), p.stat().st_mtime_ns) for p in DEMO.rglob("*") if p.is_file()
    } == before


def event(author, action, **kwargs):
    return author.dispatch({"command": "timer", "action": action, **kwargs})


def test_existing_preparation_reuses_and_recovers_without_decoding(existing, monkeypatch):
    root, run_path = existing
    items = [v for v in load_index(root) if v["video_id"] == SID]
    paths = [p for p in root.rglob("*") if p.suffix in {".mp4", ".png"}]
    before = {p: (file_hash(p), p.stat().st_mtime_ns) for p in paths}
    # A decode/extraction attempt fails this check immediately.
    import medication_video_annotation.media as media

    monkeypatch.setattr(media.av, "open", lambda *a, **k: pytest.fail("Unexpected decode"))
    assert prepare_selection(root, items, Run(run_path))[0]["status"] == "REUSED"
    run = Run(run_path)
    run.start(f"frames-{SID}", "PREPARATION", video_id=SID, activity_kind="TOOL_EXECUTION")
    assert prepare_selection(root, items, Run(run_path))[0]["status"] == "REUSED"
    row = Run(run_path).summary()["attempts"][-1]
    assert row["status"] == "COMPLETE" and row["work_seconds"] is None
    assert {p: (file_hash(p), p.stat().st_mtime_ns) for p in paths} == before
    assert not (root / "exports").exists()


def test_author_checkpoint_resume_noop_and_separate_clocks(existing):
    root, run_path = existing
    author = AuthorSession(root, run_path, SID, "synthetic-ai")
    initial = author.dispatch({"command": "get"})
    event(author, "START")
    author.dispatch(
        {
            "command": "set",
            "path": ["analysis", "categories", "people", "observations", 0, "note"],
            "value": "Synthetic readiness edit",
        }
    )
    author.dispatch({"command": "checkpoint", "cursor": 7})
    with pytest.raises(ValueError, match="Pause/finish"):
        author.dispatch({"command": "save"})
    event(author, "PAUSE", reason="USER_WAIT")
    author.close()
    resumed = AuthorSession(root, run_path, SID, "synthetic-ai")
    restored = resumed.dispatch({"command": "restore"})
    assert restored["cursor"] == 7 and restored["base_hash"] == initial["base_hash"]
    event(resumed, "RESUME")
    event(resumed, "PAUSE", reason="EXTERNAL_WAIT")
    changed = resumed.dispatch({"command": "save"})
    assert changed["revision_id"] == "revision-000001.json"
    assert resumed.dispatch({"command": "save"})["status"] == "UNCHANGED"
    event(resumed, "FINISH", result="COMPLETE")
    with pytest.raises(ValueError, match="stale"):
        resumed.dispatch({"command": "restore"})
    record = changed["record"]
    assert record["analysis"]["status"] == "DRAFT"
    assert record["label"]["coverage"]["annotation_status"] == "DRAFT"
    assert record["label"]["final_ground_truth"] is False
    assert record["label"]["evaluation_eligible"] is False
    assert record["label"]["review"]["independent_review"] == "NOT_RUN"
    assert record["label"]["review"]["reviewer_ref"] is None
    assert len(list((root / "records" / SID).glob("revision-*.json"))) == 2
    assert len(list((root / "checkpoints").rglob("*.json"))) == 1
    assert not (root / "exports").exists()
    summary = resumed.run.summary()
    assert summary["human_review_status"] == "NOT_RUN"
    ai = [a for a in summary["attempts"] if a["activity_kind"] == "INTERACTIVE_SESSION"]
    saves = [a for a in summary["attempts"] if a["activity_kind"] == "TOOL_EXECUTION"]
    assert len(ai) == 1 and len(saves) == 2
    assert not any(a["activity_kind"] == "HUMAN_SESSION" for a in summary["attempts"])
    assert ai[0]["measurement_status"] == "PARTIALLY_MEASURED"  # session gap stays unknown
    for work in (s for s in ai[0]["segments"] if s["kind"] == "WORK"):
        for save in saves:
            assert (
                work["ended_at_kst"] <= save["started_at_kst"]
                or work["started_at_kst"] >= save["ended_at_kst"]
            )


def test_author_first_valid_revision_and_basic_checks(existing):
    root, run_path = existing
    saved = root / "records" / SID / "revision-000000.json"
    value = read(saved)
    value["revision_history"] = []  # fresh draft values carry no previous authoritative history
    saved.unlink()  # isolated copy only
    saved.parent.rmdir()
    author = AuthorSession(root, run_path, SID, "synthetic-ai")
    assert author.dispatch({"command": "get"})["revision_id"] is None
    assert not saved.parent.exists()
    event(author, "START")
    event(author, "PAUSE", reason="EXTERNAL_WAIT")
    with pytest.raises(ValueError):
        author.dispatch({"command": "save"})  # blank record fails without creating a directory
    assert not saved.parent.exists()
    event(author, "RESUME")
    author.dispatch({"command": "put", "record": value})
    event(author, "PAUSE", reason="EXTERNAL_WAIT")
    result = author.dispatch({"command": "save"})
    assert result["revision_id"] == "revision-000000.json"
    assert set(result["record"]) == set(value)
    assert author.dispatch({"command": "save"})["status"] == "UNCHANGED"
    event(author, "FINISH", result="COMPLETE")
    assert [
        a["status"] for a in author.run.summary()["attempts"] if a["stage"] == "VALIDATE_SAVE"
    ] == ["FAIL", "COMPLETE", "COMPLETE"]


@pytest.mark.parametrize("scope", ["FULL_REVIEW", "DRAFT_EFFICIENCY"])
def test_efficiency_completion_is_not_human_review(tmp_path, scope):
    run = Run(create_run(tmp_path, "scope", "TRIAL", video_ids=[SID], completion_scope=scope))
    for stage in ("SELECTION", "PREPARATION", "OBSERVATION_DRAFT"):
        run.start(stage, stage, video_id=None if stage == "SELECTION" else SID)
        run.transition(stage, "FINISH", result="COMPLETE")
    with pytest.raises(ValueError, match="incomplete"):
        run.finish_run()
    run.start("save", "VALIDATE_SAVE", video_id=SID, activity_kind="TOOL_EXECUTION")
    run.transition("save", "FINISH", result="COMPLETE")
    if scope == "FULL_REVIEW":
        with pytest.raises(ValueError, match="review"):
            run.finish_run()
    else:
        run.finish_run()
        summary = run.summary()
        assert summary["status"] == "COMPLETE" and summary["completion_scope"] == scope
        assert summary["human_review_status"] == "NOT_RUN"
        assert (
            next(s for s in summary["trial_scopes"] if s["stage"] == "HUMAN_REVIEW")["seconds"]
            is None
        )


def test_author_cli_live_input_and_crash_recovery(existing):
    root, run_path = existing
    # Reproduce an abandoned clock without touching the original demo run.
    abandoned = AuthorSession(root, run_path, SID, "synthetic-ai")
    event(abandoned, "START")
    inputs = [
        {"command": "get"},
        {"command": "timer", "action": "RESUME"},
        {"command": "timer", "action": "RECOVER", "reason": "Synthetic process loss"},
        {"command": "timer", "action": "RESUME"},
        {"command": "checkpoint", "cursor": 7},
        {"command": "timer", "action": "PAUSE", "reason": "EXTERNAL_WAIT"},
        {"command": "save"},
        {"command": "timer", "action": "FINISH", "result": "COMPLETE"},
        {"command": "quit"},
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "medication_video_annotation",
            "--data-root",
            str(root),
            "author",
            "--run",
            str(run_path),
            "--id",
            SID,
            "--actor",
            "synthetic-ai",
        ],
        input="\n".join(json.dumps(v) for v in inputs) + "\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(PROJECT / "src")},
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[0]["ready"]
    assert not responses[2]["ok"] and "Resume requires" in responses[2]["error"]
    assert all(r["ok"] for i, r in enumerate(responses) if i != 2)
    assert responses[7]["value"]["status"] == "UNCHANGED"
    assert not (root / "service/.writer.lock").exists()
    assert Run(run_path).summary()["attempts"][0]["work_seconds"] is None
