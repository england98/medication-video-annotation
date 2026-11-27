"""Append-only run events with process-local clocks and explicit unknown intervals."""

import argparse
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

from .io import atomic_json, identifier, now, read, require, timestamp, writer_lock

COMMON = {"REPORT", "TOOL_FIX"}
SETUP = {"SETUP_HANDOFF", "SETUP_IMPLEMENTATION", "SETUP_VALIDATION"} | COMMON
TRIAL_COMMON = {"SELECTION"} | COMMON
VIDEO = {
    "PREPARATION",
    "OBSERVATION_DRAFT",
    "VALIDATE_SAVE",
    "HUMAN_REVIEW",
    "CORRECTION",
    "EXPORT",
}
WAITS = {"USER_WAIT", "USAGE_LIMIT", "RETRY_WAIT", "EXTERNAL_WAIT", "EXPLICIT_PAUSE"}
TERMINAL = {"COMPLETE", "FAIL", "CANCELLED"}


def validate_task(meta, task):
    identifier(task["task_id"])
    stage, video = task["stage"], task.get("video_id")
    if meta["run_kind"] == "SETUP":
        require(stage in SETUP and video is None, "Setup stages are common, not per-video")
    else:
        require(stage in TRIAL_COMMON | VIDEO, "Invalid trial stage")
        require(
            (stage in TRIAL_COMMON and video is None)
            or (stage in VIDEO and video in meta["video_ids"]),
            "Invalid video/stage scope",
        )
    require(
        task.get("activity_kind", "INTERACTIVE_SESSION")
        in {"INTERACTIVE_SESSION", "TOOL_EXECUTION", "HUMAN_SESSION"},
        "Invalid activity kind",
    )


def create_run(
    parent,
    run_id,
    kind,
    *,
    video_ids=(),
    plan=(),
    started_at_kst=None,
    completion_scope="FULL_REVIEW",
):
    identifier(run_id)
    require(kind in {"SETUP", "TRIAL", "MIGRATION", "REVIEW"}, "Invalid run kind")
    require(completion_scope in {"FULL_REVIEW", "DRAFT_EFFICIENCY"}, "Invalid completion scope")
    require(kind == "TRIAL" or completion_scope == "FULL_REVIEW", "Trial scope requires TRIAL")
    require(len(set(video_ids)) == len(video_ids), "Duplicate video IDs")
    for sid in video_ids:
        identifier(sid)
    require(kind != "SETUP" or not video_ids, "Setup cannot have real video targets")
    meta = {
        "format": "onc110-run-v0.1",
        "run_id": run_id,
        "run_kind": kind,
        "started_at_kst": started_at_kst or now(),
        "timezone": "Asia/Seoul",
        "video_ids": list(video_ids),
        "plan": list(plan),
        "completion_scope": completion_scope if kind == "TRIAL" else kind,
    }
    timestamp(meta["started_at_kst"])
    require(len({t["task_id"] for t in plan}) == len(plan), "Duplicate planned tasks")
    for task in plan:
        validate_task(meta, task)
    directory = Path(parent) / run_id
    directory.mkdir(parents=True, exist_ok=False)
    atomic_json(directory / "run.json", meta, immutable=True)
    (directory / "progress.jsonl").touch(exist_ok=False)
    Run(directory).summary()
    return directory


class Run:
    def __init__(self, directory, *, clock=time.perf_counter, wall=now):
        self.directory = Path(directory)
        self.meta = read(self.directory / "run.json")
        require(
            self.meta.get("format") == "onc110-run-v0.1",
            "Legacy setup logs remain read-only; create a new versioned run",
        )
        self.clock, self.wall = clock, wall
        self.session_id = str(uuid.uuid4())
        self.marks = {}

    def events(self):
        text = (self.directory / "progress.jsonl").read_text(encoding="utf-8")
        require(
            not text or text.endswith("\n"),
            "Incomplete journal tail; preserve and repair explicitly",
        )
        events = [json.loads(line) for line in text.splitlines()]
        require(
            [e["sequence"] for e in events] == list(range(1, len(events) + 1)),
            "Broken journal sequence",
        )
        require(all(e["run_id"] == self.meta["run_id"] for e in events), "Wrong run journal")
        return events

    def _append(self, event, events):
        event = {
            "run_id": self.meta["run_id"],
            "sequence": len(events) + 1,
            "session_id": self.session_id,
            "pid": os.getpid(),
            **event,
        }
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
        with (self.directory / "progress.jsonl").open("a", encoding="utf-8") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        # The journal is authoritative. Summary can always be rebuilt after a crash.
        self._summary(events + [event])
        return event

    def _open_events(self):
        events = self.events()
        require(not any(e["event"] == "RUN_END" for e in events), "Run already ended")
        return events

    def select_targets(self, video_ids):
        """Record confirmed targets after timed selection, without creating video records."""
        require(self.meta["run_kind"] == "TRIAL", "Targets belong to a trial run")
        require(video_ids and len(set(video_ids)) == len(video_ids), "Unique targets required")
        for sid in video_ids:
            identifier(sid)
        with writer_lock(self.directory):
            events = self._open_events()
            require(
                not self.meta["video_ids"]
                and not any(e["event"] == "TARGETS_SELECTED" for e in events),
                "Targets already fixed",
            )
            require(
                any(e.get("stage") == "SELECTION" and e["state"] == "COMPLETE" for e in events),
                "Complete timed selection first",
            )
            event = self._append(
                dict(
                    event="TARGETS_SELECTED",
                    state="RECORDED",
                    at_kst=self.wall(),
                    video_ids=list(video_ids),
                    task_id=None,
                    video_id=None,
                    stage="SELECTION",
                    attempt=None,
                    result="COMPLETE",
                    reason=None,
                    measurement_method="NOT_APPLICABLE",
                    segment=None,
                ),
                events,
            )
            self.meta["video_ids"] = list(video_ids)
            return event

    def _refresh_targets(self):
        for event in self.events():
            if event["event"] == "TARGETS_SELECTED":
                self.meta["video_ids"] = event["video_ids"]

    def start(
        self,
        task_id,
        stage,
        *,
        video_id=None,
        activity_kind="INTERACTIVE_SESSION",
        retry_reason=None,
        actor_ref=None,
    ):
        task = dict(task_id=task_id, stage=stage, video_id=video_id, activity_kind=activity_kind)
        self._refresh_targets()
        validate_task(self.meta, task)
        with writer_lock(self.directory):
            events = self._open_events()
            history = [e for e in events if e.get("task_id") == task_id]
            planned = next((t for t in self.meta["plan"] if t["task_id"] == task_id), None)
            identity = history[0] if history else planned
            if identity:
                require(
                    all(
                        task[k] == identity.get(k, "INTERACTIVE_SESSION")
                        for k in ("stage", "video_id", "activity_kind")
                    ),
                    "Task identity changed",
                )
            if history:
                require(
                    history[-1]["state"] in TERMINAL and retry_reason,
                    "Prior attempt must finish; retry/rework reason required",
                )
            attempt = history[-1]["attempt"] + 1 if history else 1
            at = self.wall()
            event = self._append(
                {
                    **task,
                    "attempt": attempt,
                    "event": "START",
                    "state": "ACTIVE",
                    "started_at_kst": at,
                    "at_kst": at,
                    "result": None,
                    "reason": retry_reason,
                    "actor_ref": actor_ref,
                    "measurement_method": "MONOTONIC_PENDING",
                    "segment": None,
                },
                events,
            )
            self.marks[task_id] = (event["sequence"], self.clock())
            return event

    def transition(self, task_id, action, *, reason=None, result=None):
        require(action in {"PAUSE", "RESUME", "FINISH", "INTERRUPT", "RECOVER"}, "Invalid action")
        with writer_lock(self.directory):
            events = self._open_events()
            history = [e for e in events if e.get("task_id") == task_id]
            require(history, "Unknown task")
            old = history[-1]
            state = old["state"]
            require(state not in TERMINAL, "Attempt already ended")
            if action == "PAUSE":
                require(
                    state == "ACTIVE" and reason in WAITS, "Pause requires active task and reason"
                )
                new_state = "PAUSED"
            elif action == "RESUME":
                require(
                    state in {"PAUSED", "INTERRUPTED"}, "Resume requires paused/interrupted task"
                )
                new_state = "ACTIVE"
            elif action in {"INTERRUPT", "RECOVER"}:
                require(state in {"ACTIVE", "PAUSED"} and reason, "Interruption reason required")
                new_state = "INTERRUPTED"
            else:
                require(state in {"ACTIVE", "PAUSED"} and result in TERMINAL, "Invalid finish")
                require(result == "COMPLETE" or reason, "Failure/cancellation reason required")
                new_state = result
            mark = self.marks.get(task_id)
            local = mark is not None and mark[0] == old["sequence"]
            if action == "RECOVER":
                require(not local, "Use INTERRUPT for the live local session")
            elif state != "INTERRUPTED":
                require(local, "Clock session lost; RECOVER explicitly before resuming")
            at = self.wall()
            measured = local and state != "INTERRUPTED" and action != "RECOVER"
            seconds = self.clock() - mark[1] if measured else None
            require(seconds is None or math.isfinite(seconds) and seconds >= 0, "Invalid duration")
            segment = {
                "kind": "WORK"
                if state == "ACTIVE"
                else "WAIT"
                if state == "PAUSED"
                else "UNMEASURED_GAP",
                "reason": old.get("reason") if state != "ACTIVE" else None,
                "started_at_kst": old["at_kst"],
                "ended_at_kst": at,
                "seconds": seconds,
                "measurement_method": "MONOTONIC_SAME_SESSION" if measured else "NOT_MEASURED",
            }
            event = {
                k: old[k]
                for k in (
                    "task_id",
                    "stage",
                    "video_id",
                    "activity_kind",
                    "attempt",
                    "started_at_kst",
                )
            }
            event.update(
                event=action,
                state=new_state,
                at_kst=at,
                segment=segment,
                result=result,
                reason=reason,
                measurement_method=segment["measurement_method"],
                actor_ref=old.get("actor_ref"),
            )
            saved = self._append(event, events)
            if new_state in {"ACTIVE", "PAUSED"}:
                self.marks[task_id] = (saved["sequence"], self.clock())
            else:
                self.marks.pop(task_id, None)
            return saved

    def record_external(
        self,
        task_id,
        stage,
        *,
        started_at_kst,
        ended_at_kst,
        seconds,
        measurement_method,
        provenance,
        result="COMPLETE",
        video_id=None,
        activity_kind="INTERACTIVE_SESSION",
        reason=None,
        retry_reason=None,
    ):
        """Import a real external clock reading, or explicit NOT_MEASURED; never infer one."""
        task = dict(task_id=task_id, stage=stage, video_id=video_id, activity_kind=activity_kind)
        self._refresh_targets()
        validate_task(self.meta, task)
        require(provenance and result in TERMINAL, "Provenance and terminal result required")
        require(result == "COMPLETE" or reason, "Failure/cancellation reason required")
        require(measurement_method in {"EXTERNAL_MONOTONIC", "NOT_MEASURED"}, "Invalid method")
        require(
            (seconds is None and measurement_method == "NOT_MEASURED")
            or (
                type(seconds) in (int, float)
                and math.isfinite(seconds)
                and seconds >= 0
                and measurement_method == "EXTERNAL_MONOTONIC"
            ),
            "Seconds/method mismatch",
        )
        if started_at_kst is None or ended_at_kst is None:
            require(measurement_method == "NOT_MEASURED", "Measured intervals need timestamps")
            for value in (started_at_kst, ended_at_kst):
                if value is not None:
                    timestamp(value)
        else:
            require(timestamp(ended_at_kst) >= timestamp(started_at_kst), "Invalid time interval")
        with writer_lock(self.directory):
            events = self._open_events()
            history = [e for e in events if e.get("task_id") == task_id]
            if history:
                require(
                    history[-1]["state"] in TERMINAL and retry_reason,
                    "Terminal prior attempt and explicit retry reason required",
                )
                require(
                    all(task[k] == history[0][k] for k in ("stage", "video_id", "activity_kind")),
                    "Task identity changed",
                )
            attempt = history[-1]["attempt"] + 1 if history else 1
            segment = dict(
                kind="WORK",
                reason=reason,
                started_at_kst=started_at_kst,
                ended_at_kst=ended_at_kst,
                seconds=seconds,
                measurement_method=measurement_method,
            )
            return self._append(
                {
                    **task,
                    "attempt": attempt,
                    "event": "EXTERNAL_INTERVAL",
                    "state": result,
                    "started_at_kst": started_at_kst,
                    "at_kst": ended_at_kst,
                    "recorded_at_kst": self.wall(),
                    "segment": segment,
                    "result": result,
                    "reason": reason,
                    "measurement_method": measurement_method,
                    "provenance": provenance,
                    "retry_reason": retry_reason,
                },
                events,
            )

    def finish_run(self, result="COMPLETE"):
        require(result in {"COMPLETE", "FAILED", "CANCELLED"}, "Invalid run result")
        with writer_lock(self.directory):
            events = self._open_events()
            latest = {e["task_id"]: e for e in events if e.get("task_id")}
            require(
                latest and all(e["state"] in TERMINAL for e in latest.values()),
                "Open/interrupted attempts remain",
            )
            if result == "COMPLETE":
                require(
                    all(s["status"] == "COMPLETE" for s in self._scopes(events) if s["required"]),
                    "Required trial stages/review remain incomplete",
                )
                require(
                    all(e["state"] == "COMPLETE" for e in latest.values())
                    and all(t["task_id"] in latest for t in self.meta["plan"]),
                    "Failed/unexecuted tasks remain; cannot complete run",
                )
            return self._append(
                dict(
                    event="RUN_END",
                    state=result,
                    at_kst=self.wall(),
                    task_id=None,
                    video_id=None,
                    stage=None,
                    attempt=None,
                    result=result,
                    reason=None,
                    measurement_method="KST_WALL_CLOCK",
                    segment=None,
                ),
                events,
            )

    def summary(self):
        with writer_lock(self.directory):
            return self._summary(self.events())

    def _scopes(self, events):
        if self.meta["run_kind"] not in {"TRIAL", "MIGRATION", "REVIEW"}:
            return []
        targets = self.meta["video_ids"]
        for event in events:
            if event["event"] == "TARGETS_SELECTED":
                targets = event["video_ids"]
        scopes = [] if self.meta["run_kind"] == "REVIEW" else [("SELECTION", None, True)]
        for sid in targets:
            scopes.extend(
                (
                    stage,
                    sid,
                    (
                        stage == "HUMAN_REVIEW"
                        if self.meta["run_kind"] == "REVIEW"
                        else stage in {"PREPARATION", "VALIDATE_SAVE"}
                        if self.meta["run_kind"] == "MIGRATION"
                        else stage not in {"CORRECTION", "EXPORT"}
                        and not (
                            stage == "HUMAN_REVIEW"
                            and self.meta.get("completion_scope") == "DRAFT_EFFICIENCY"
                        )
                    ),
                )
                for stage in sorted(VIDEO)
            )
        rows = []
        for stage, sid, required in scopes:
            relevant = [
                e
                for e in events
                if e.get("task_id") and e.get("stage") == stage and e.get("video_id") == sid
            ]
            latest = {e["task_id"]: e for e in relevant}
            status = (
                (
                    "COMPLETE"
                    if all(e["state"] == "COMPLETE" for e in latest.values())
                    else "INCOMPLETE"
                )
                if latest
                else "NOT_RUN"
            )
            rows.append(
                dict(
                    stage=stage,
                    video_id=sid,
                    required=required,
                    status=status,
                    seconds=None,
                    timing_ref="attempts; no overlapping totals inferred",
                )
            )
        if not targets:
            rows.append(
                dict(
                    stage="TARGETS_SELECTED",
                    video_id=None,
                    required=True,
                    status="NOT_RUN",
                    seconds=None,
                )
            )
        return rows

    def _summary(self, events):
        groups = {}
        for event in events:
            if event.get("task_id"):
                groups.setdefault((event["task_id"], event["attempt"]), []).append(event)
        attempts = []
        for history in groups.values():
            first, last = history[0], history[-1]
            segments = [e["segment"] for e in history if e.get("segment")]
            work = [s for s in segments if s["kind"] == "WORK"]
            unknown = any(s["seconds"] is None for s in segments)
            complete = last["state"] in TERMINAL
            known = sum(s["seconds"] for s in work if s["seconds"] is not None)
            attempts.append(
                {
                    **{
                        k: first[k]
                        for k in (
                            "task_id",
                            "stage",
                            "video_id",
                            "attempt",
                            "activity_kind",
                            "started_at_kst",
                        )
                    },
                    "ended_at_kst": last["at_kst"] if complete else None,
                    "status": last["state"],
                    "result": last["result"],
                    "reason": last["reason"],
                    "known_work_seconds": known if work else None,
                    "work_seconds": known if complete and not unknown else None,
                    "measurement_status": "MEASURED"
                    if complete and not unknown
                    else "PARTIALLY_MEASURED"
                    if any(s["seconds"] is not None for s in segments)
                    else "NOT_MEASURED",
                    "segments": segments,
                }
            )
        present = {a["task_id"] for a in attempts}
        not_run = [
            {**t, "status": "NOT_RUN", "seconds": None}
            for t in self.meta["plan"]
            if t["task_id"] not in present
        ]
        finish = next((e for e in events if e["event"] == "RUN_END"), None)
        observed_at = finish["at_kst"] if finish else self.wall()
        wall = (timestamp(observed_at) - timestamp(self.meta["started_at_kst"])).total_seconds()
        scopes = self._scopes(events)
        reviews = [s for s in scopes if s["stage"] == "HUMAN_REVIEW"]
        result = {
            "format": "onc110-run-summary-v0.1",
            "run_id": self.meta["run_id"],
            "run_kind": self.meta["run_kind"],
            "status": finish["result"] if finish else "OPEN",
            "completion_scope": self.meta.get(
                "completion_scope", "FULL_REVIEW" if self.meta["run_kind"] == "TRIAL" else "SETUP"
            ),
            "human_review_status": (
                "COMPLETE"
                if reviews and all(s["status"] == "COMPLETE" for s in reviews)
                else "NOT_RUN"
                if not any(s["status"] != "NOT_RUN" for s in reviews)
                else "INCOMPLETE"
            ),
            "started_at_kst": self.meta["started_at_kst"],
            "ended_at_kst": finish["at_kst"] if finish else None,
            "observed_at_kst": observed_at,
            "wall_elapsed_seconds": wall if wall >= 0 else None,
            "wall_measurement_method": "KST_WALL_CLOCK" if wall >= 0 else "CLOCK_ANOMALY",
            "attempts": attempts,
            "not_run": not_run,
            "trial_scopes": scopes,
            "aggregation_note": "Attempt/segment durations may overlap. Never sum them as wall time. "
            "Common work is not allocated per video. WORK is session/tool duration, not model inference.",
            "source_event_count": len(events),
        }
        atomic_json(self.directory / "summary.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--parent", type=Path, default=Path("local/runs"))
    create.add_argument("--id", required=True)
    create.add_argument("--kind", choices=["SETUP", "TRIAL", "MIGRATION", "REVIEW"], required=True)
    create.add_argument("--video-id", action="append", default=[])
    create.add_argument(
        "--completion-scope", choices=["FULL_REVIEW", "DRAFT_EFFICIENCY"], default="FULL_REVIEW"
    )
    for name in ("session", "summary"):
        command = sub.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        print(
            create_run(
                args.parent,
                args.id,
                args.kind,
                video_ids=args.video_id,
                completion_scope=args.completion_scope,
            )
        )
        return
    run = Run(args.run)
    if args.command == "summary":
        print(json.dumps(run.summary(), ensure_ascii=False))
        return
    # Keep this process alive for monotonic start/pause/resume/finish measurements.
    # EOF does not invent a completion. A new process must RECOVER outstanding tasks.
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request.pop("command")
            method = {
                "start": run.start,
                "transition": run.transition,
                "select_targets": run.select_targets,
                "finish_run": run.finish_run,
                "summary": run.summary,
            }[action]
            value = method(**request)
            print(json.dumps({"ok": True, "value": value}, ensure_ascii=False), flush=True)
        except (ValueError, KeyError, TypeError) as error:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
