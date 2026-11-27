from datetime import datetime, timedelta, timezone

import pytest

from medication_video_annotation.io import read
from medication_video_annotation.timing import Run, create_run


class Clock:
    def __init__(self):
        self.seconds = 0

    def tick(self):
        return self.seconds

    def wall(self):
        return (
            datetime(2026, 10, 17, tzinfo=timezone(timedelta(hours=9)))
            + timedelta(seconds=self.seconds)
        ).isoformat()

    def advance(self, seconds):
        self.seconds += seconds


def make_run(tmp_path, kind="SETUP", plan=(), videos=()):
    clock = Clock()
    folder = create_run(
        tmp_path, "TEST-RUN", kind, video_ids=videos, plan=plan, started_at_kst=clock.wall()
    )
    return clock, Run(folder, clock=clock.tick, wall=clock.wall)


def test_pause_resume_failure_retry_and_wall(tmp_path):
    c, run = make_run(tmp_path)
    run.start("build", "SETUP_IMPLEMENTATION")
    c.advance(10)
    run.transition("build", "PAUSE", reason="USAGE_LIMIT")
    c.advance(20)
    run.transition("build", "RESUME")
    c.advance(5)
    run.transition("build", "FINISH", result="FAIL", reason="Synthetic failure")
    run.start("build", "SETUP_IMPLEMENTATION", retry_reason="Fix and retry")
    c.advance(3)
    run.transition("build", "FINISH", result="COMPLETE")
    run.finish_run()
    summary = run.summary()
    assert summary["wall_elapsed_seconds"] == 38
    assert [a["work_seconds"] for a in summary["attempts"]] == [15, 3]
    assert summary["attempts"][0]["segments"][1]["seconds"] == 20
    assert summary["attempts"][0]["segments"][1]["reason"] == "USAGE_LIMIT"
    assert [a["attempt"] for a in summary["attempts"]] == [1, 2]


def test_restart_keeps_unknown_and_measured_segments(tmp_path):
    c, run = make_run(tmp_path)
    run.start("build", "SETUP_IMPLEMENTATION")
    c.advance(4)
    run.transition("build", "PAUSE", reason="USER_WAIT")
    c.advance(100)
    reopened = Run(run.directory, clock=lambda: 999, wall=c.wall)
    with pytest.raises(ValueError, match="RECOVER"):
        reopened.transition("build", "RESUME")
    reopened.transition("build", "RECOVER", reason="Process restarted")
    c.advance(50)
    reopened.transition("build", "RESUME")
    reopened.transition("build", "FINISH", result="COMPLETE")
    row = reopened.summary()["attempts"][0]
    assert row["work_seconds"] is None and row["known_work_seconds"] == 4
    assert [s["seconds"] for s in row["segments"]] == [4, None, None, 0]
    assert row["measurement_status"] == "PARTIALLY_MEASURED"


def test_not_run_and_scope(tmp_path):
    plan = [{"task_id": "review", "stage": "HUMAN_REVIEW", "video_id": "SYNTH-1"}]
    _, run = make_run(tmp_path, "TRIAL", plan=plan, videos=["SYNTH-1"])
    assert run.summary()["not_run"][0]["seconds"] is None
    with pytest.raises(ValueError):
        run.start("bad", "SELECTION", video_id="SYNTH-1")
    with pytest.raises(ValueError):
        run.start("bad", "SETUP_IMPLEMENTATION")
    with pytest.raises(ValueError):
        run.finish_run()


def test_parallel_work_is_not_wall(tmp_path):
    c, run = make_run(tmp_path)
    run.start("a", "SETUP_IMPLEMENTATION")
    run.start("b", "SETUP_VALIDATION", activity_kind="TOOL_EXECUTION")
    c.advance(10)
    run.transition("a", "FINISH", result="COMPLETE")
    run.transition("b", "FINISH", result="COMPLETE")
    run.finish_run()
    summary = run.summary()
    assert summary["wall_elapsed_seconds"] == 10
    assert [a["work_seconds"] for a in summary["attempts"]] == [10, 10]
    assert "total_active_seconds" not in summary


def test_trial_begins_before_selection_then_targets(tmp_path):
    c, run = make_run(tmp_path, "TRIAL")
    run.start("select", "SELECTION")
    c.advance(10)
    run.transition("select", "FINISH", result="COMPLETE")
    run.select_targets(["SYNTH-1"])
    reopened = Run(run.directory, clock=c.tick, wall=c.wall)
    reopened.start("observe", "OBSERVATION_DRAFT", video_id="SYNTH-1")
    c.advance(2)
    reopened.transition("observe", "FINISH", result="COMPLETE")
    assert reopened.summary()["attempts"][1]["work_seconds"] == 2
    assert read(run.directory / "run.json")["video_ids"] == []


def test_summary_rebuilt_and_torn_log_fails_closed(tmp_path):
    c, run = make_run(tmp_path)
    run.start("a", "SETUP_IMPLEMENTATION")
    c.advance(2)
    run.transition("a", "FINISH", result="COMPLETE")
    before = run.summary()
    (run.directory / "summary.json").write_text("{}", encoding="utf-8")
    assert run.summary() == before
    with (run.directory / "progress.jsonl").open("a", encoding="utf-8") as out:
        out.write("{incomplete")
    with pytest.raises(ValueError, match="Incomplete journal"):
        run.summary()


def test_external_unknown_and_finite_values(tmp_path):
    c, run = make_run(tmp_path)
    run.record_external(
        "gap",
        "SETUP_HANDOFF",
        started_at_kst=c.wall(),
        ended_at_kst=c.wall(),
        seconds=None,
        measurement_method="NOT_MEASURED",
        provenance="No timer",
    )
    assert run.summary()["attempts"][0]["work_seconds"] is None
    with pytest.raises(ValueError):
        run.record_external(
            "bad",
            "SETUP_HANDOFF",
            started_at_kst=c.wall(),
            ended_at_kst=c.wall(),
            seconds=float("nan"),
            measurement_method="EXTERNAL_MONOTONIC",
            provenance="Invalid timer",
        )


def test_open_attempt_cannot_be_completed_or_retried(tmp_path):
    _, run = make_run(tmp_path)
    run.start("a", "SETUP_IMPLEMENTATION")
    with pytest.raises(ValueError):
        run.finish_run()
    with pytest.raises(ValueError):
        run.start("a", "SETUP_IMPLEMENTATION", retry_reason="Not closed")
    assert run.summary()["attempts"][0]["work_seconds"] is None


def test_trial_cannot_complete_before_review(tmp_path):
    _, run = make_run(tmp_path, "TRIAL", videos=["SYNTH-1"])
    run.start("selection", "SELECTION")
    run.transition("selection", "FINISH", result="COMPLETE")
    with pytest.raises(ValueError, match="review"):
        run.finish_run()
    for stage in ("PREPARATION", "OBSERVATION_DRAFT", "VALIDATE_SAVE", "HUMAN_REVIEW"):
        run.start(stage, stage, video_id="SYNTH-1")
        run.transition(stage, "FINISH", result="COMPLETE")
    run.finish_run()
    summary = run.summary()
    export = next(s for s in summary["trial_scopes"] if s["stage"] == "EXPORT")
    assert export["status"] == "NOT_RUN" and export["seconds"] is None


def test_unmeasured_failure_without_invented_timestamps(tmp_path):
    _, run = make_run(tmp_path)
    params = dict(
        started_at_kst=None,
        ended_at_kst=None,
        seconds=None,
        measurement_method="NOT_MEASURED",
        provenance="Observed failure; timer absent",
    )
    run.record_external("fix", "TOOL_FIX", **params, result="FAIL", reason="Filesystem unsupported")
    run.record_external("fix", "TOOL_FIX", **params, retry_reason="Implemented supported operation")
    run.finish_run()
    rows = run.summary()["attempts"]
    assert rows[0]["started_at_kst"] is None and rows[0]["ended_at_kst"] is None
    assert [r["attempt"] for r in rows] == [1, 2]
    assert all(r["work_seconds"] is None for r in rows)
