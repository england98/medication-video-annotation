"""Loopback-only review workbench, adapted from onc110_pilot.make_server."""

import json
import mimetypes
import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .behavior_profile import human_reviewed, profile_of
from .io import now, read, relative, require
from .media import playback, verify_prepared
from .operations import latest_task, operation
from .records import instantiate, load_index, record_hash, save_revision
from .timing import TERMINAL, Run
from .workflow import (
    complete_review,
    export_selected,
    latest,
    normalize_edit,
    save_checkpoint,
    save_with_review,
)

WEB = Path(__file__).resolve().parents[2] / "web/review.html"


class Workbench:
    def __init__(self, root, run_path, ids):
        self.root = Path(root).resolve(strict=True)
        self.run = Run(run_path)
        self.run._refresh_targets()
        self.items = {v["video_id"]: v for v in load_index(self.root) if v["video_id"] in ids}
        require(
            ids and len(set(ids)) == len(ids) and set(ids) == set(self.items),
            "Unknown/duplicate selected ID",
        )
        require(set(ids) <= set(self.run.meta["video_ids"]), "Selected IDs outside run")
        self.ids, self.lock, self.token = list(ids), threading.RLock(), secrets.token_urlsafe(32)
        self.maps = {sid: verify_prepared(self.root, item) for sid, item in self.items.items()}

    def timer_state(self, sid, stage):
        task_id = f"{stage.lower()}-{sid}"
        event = latest_task(self.run, task_id)
        if not event:
            return {"state": "NOT_RUN", "task_id": task_id}
        state = event["state"]
        if state in {"ACTIVE", "PAUSED"} and task_id not in self.run.marks:
            state = "NEEDS_RECOVERY"
        segments = [
            e["segment"]
            for e in self.run.events()
            if e.get("task_id") == task_id
            and e.get("attempt") == event["attempt"]
            and e.get("segment")
        ]
        work_seconds = sum(
            s["seconds"] for s in segments if s["kind"] == "WORK" and s["seconds"] is not None
        )
        wait_seconds = sum(
            s["seconds"] for s in segments if s["kind"] == "WAIT" and s["seconds"] is not None
        )
        if state in {"ACTIVE", "PAUSED"}:
            elapsed = self.run.clock() - self.run.marks[task_id][1]
            if state == "ACTIVE":
                work_seconds += elapsed
            else:
                wait_seconds += elapsed
        return {
            "state": state,
            "task_id": task_id,
            "attempt": event["attempt"],
            "actor_ref": event.get("actor_ref"),
            "at_kst": event["at_kst"],
            "known_work_seconds": work_seconds,
            "known_wait_seconds": wait_seconds,
            "has_unmeasured": state == "NEEDS_RECOVERY"
            or any(s["seconds"] is None for s in segments),
        }

    def record(self, sid):
        item, mapping = self.items[sid], self.maps[sid]
        record, path = latest(self.root, item)
        checkpoint_path = relative(self.root, f"checkpoints/{sid}.json")
        checkpoint = read(checkpoint_path) if checkpoint_path.exists() else None
        base = record_hash(record) if record else None
        record = record or instantiate(sid, item["source_sha256"], item["frame_map_ref"])
        return {
            "record": record,
            "semantic_profile": profile_of(record),
            "base_hash": base,
            "revision_id": path.name if path else None,
            "frames": mapping["frames"],
            "width": mapping["width"],
            "height": mapping["height"],
            "filename": item["original_filename"],
            "video_time_offset_ms": mapping["first_source_ms"]
            if item.get("imported_revision")
            else 0,
            "checkpoint": checkpoint,
            "checkpoint_hash": record_hash(checkpoint) if checkpoint else None,
            "checkpoint_stale": checkpoint is not None and checkpoint["base_hash"] != base,
            "checkpoint_profile": checkpoint.get("semantic_profile") if checkpoint else None,
            "timers": {
                s: self.timer_state(sid, s)
                for s in ("HUMAN_REVIEW", "OBSERVATION_DRAFT", "CORRECTION")
            },
        }

    def timer(self, sid, value):
        stage, actor, action = value["stage"], value["actor_ref"].strip(), value["action"]
        require(
            stage in {"HUMAN_REVIEW", "OBSERVATION_DRAFT", "CORRECTION"} and actor,
            "Invalid timer stage/actor",
        )
        task_id = f"{stage.lower()}-{sid}"
        old = latest_task(self.run, task_id)
        if old and old["state"] not in TERMINAL:
            require(old.get("actor_ref") == actor, "Timer belongs to another actor")
        if action == "START":
            require(
                not old or old["state"] in TERMINAL, "Timer already started; resume/recover instead"
            )
            self.run.start(
                task_id,
                stage,
                video_id=sid,
                activity_kind="HUMAN_SESSION",
                actor_ref=actor,
                retry_reason="Additional review/correction session" if old else None,
            )
        elif action == "FINISH":
            # Timing is optional and independent of the saved annotation/review state.
            self.run.transition(task_id, "FINISH", result="COMPLETE")
        elif action == "CANCEL":
            self.run.transition(
                task_id, "FINISH", result="CANCELLED", reason="User ended incomplete session"
            )
        else:
            require(action in {"PAUSE", "RESUME", "RECOVER"}, "Unknown timer action")
            reason = value.get("reason")
            self.run.transition(task_id, action, reason=reason)
        return self.timer_state(sid, stage)

    def write(self, action, sid, value):
        with self.lock:
            item, mapping = self.items[sid], self.maps[sid]
            if action == "timer":
                return self.timer(sid, value)
            actor = value.get("actor_ref", "").strip()
            require(actor, "Actor required")
            if action == "checkpoint":
                require(value["cursor"] < mapping["frame_count"], "Cursor outside video")
                return save_checkpoint(
                    self.root,
                    item,
                    value["record"],
                    base_hash=value["base_hash"],
                    checkpoint_hash=value.get("checkpoint_hash"),
                    actor=actor,
                    cursor=value["cursor"],
                    note=value.get("note", ""),
                    mapping=mapping,
                    replace_reason=value.get("replace_reason"),
                )
            if action == "save":
                with operation(self.run, f"save-{sid}", "VALIDATE_SAVE", sid):
                    before, _ = latest(self.root, item)
                    if "review_status" in value:
                        result = save_with_review(
                            self.root, item, mapping, value["record"],
                            base_hash=value["base_hash"], actor=actor,
                            review_status=value["review_status"],
                            reviewer_ref=value.get("reviewer_ref", ""),
                            reviewed_ranges=value.get("reviewed_ranges", []),
                        )
                    else:
                        # Keep author/older clients' draft-only save semantics.
                        record = normalize_edit(before, value["record"], actor)
                        result = save_revision(
                            self.root, item, mapping, record, base_hash=value["base_hash"], actor=actor
                        )
                if (
                    "review_status" not in value
                    and before
                    # A new profile's unreviewed start does not invalidate the v0.1 history.
                    and profile_of(before) == profile_of(result["record"])
                    and human_reviewed(before)
                    and not human_reviewed(result["record"])
                ):
                    task_id = f"human_review-{sid}"
                    old = latest_task(self.run, task_id)
                    if not old or old["state"] in TERMINAL:
                        self.run.record_external(
                            task_id,
                            "HUMAN_REVIEW",
                            video_id=sid,
                            activity_kind="HUMAN_SESSION",
                            started_at_kst=now(),
                            ended_at_kst=now(),
                            seconds=None,
                            measurement_method="NOT_MEASURED",
                            provenance="Record invalidation",
                            result="CANCELLED",
                            reason="Content changed; human re-review required",
                            retry_reason="Invalidate prior review" if old else None,
                        )
                return {**result, "revision_id": Path(result["path"]).name}
            if action == "review":
                state = self.timer_state(sid, "HUMAN_REVIEW")
                require(
                    state["state"] in {"ACTIVE", "PAUSED"}, "Start/resume the review timer first"
                )
                require(state.get("actor_ref") == actor, "Review timer actor differs")
                result = complete_review(
                    self.root,
                    item,
                    mapping,
                    base_hash=value["base_hash"],
                    actor=actor,
                    reviewed_ranges=value["reviewed_ranges"],
                    exact_event_ids=value.get("exact_event_ids", []),
                )
                self.run.transition(
                    state["task_id"],
                    "FINISH",
                    result="COMPLETE",
                    reason="Explicit full-clip human review saved",
                )
                return {**result, "revision_id": Path(result["path"]).name}
            if action == "export":
                require(
                    all(
                        self.timer_state(sid, s)["state"]
                        not in {"ACTIVE", "PAUSED", "INTERRUPTED", "NEEDS_RECOVERY"}
                        for s in ("HUMAN_REVIEW", "OBSERVATION_DRAFT", "CORRECTION")
                    ),
                    "Finish open timing sessions before export",
                )
                with operation(self.run, f"export-{sid}", "EXPORT", sid):
                    return export_selected(
                        self.root,
                        item,
                        mapping,
                        revision_id=value["revision_id"],
                        expected_hash=value["base_hash"],
                        request=value.get("request"),
                    )
            raise ValueError("Unknown operation")


class LocalHandler(BaseHTTPRequestHandler):
    """Loopback same-origin checks, JSON/bytes responses and byte-range media."""

    def allowed(self):
        allowed = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        return self.headers.get("Host", "") in allowed and self.headers.get("Origin") in (
            None,
            *("http://" + host for host in allowed),
        )

    def respond(self, data, status=200, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def media(self, path):
        total = path.stat().st_size
        start, end = 0, total - 1
        byte_range = self.headers.get("Range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
            if not match:
                self.respond({"error": "Unsupported byte range"}, 416)
                return
            start, end = int(match[1]), min(int(match[2]) if match[2] else end, end)
            if not 0 <= start <= end < total:
                self.respond({"error": "Invalid byte range"}, 416)
                return
        self.send_response(206 if byte_range else 200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


def loopback_server(server_class, handler, port):
    server = server_class(("127.0.0.1", port), handler, bind_and_activate=False)
    server.allow_reuse_address = False
    server.daemon_threads = True
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        server.server_bind()
        server.server_activate()
    except Exception:
        server.server_close()
        raise
    return server


def make_server(root, run_path, ids, port=8767, *, selection=None):
    bench = Workbench(root, run_path, ids)

    class Handler(LocalHandler):
        def do_GET(self):
            if not self.allowed():
                self.respond({"error": "Local same-origin access only"}, 403)
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                if parts == [""]:
                    self.respond(WEB.read_bytes(), content_type="text/html; charset=utf-8")
                elif parts == ["api", "index"]:
                    self.respond(
                        {
                            "application": "medication-video-annotation",
                            "data_root": str(bench.root),
                            "ids": bench.ids,
                            "classifications": {
                                sid: bench.items[sid]["classification"] for sid in bench.ids
                            },
                            "token": bench.token,
                            "run_id": bench.run.meta["run_id"],
                            "selection_mode": selection["mode"] if selection else "selected",
                        }
                    )
                elif parts == ["api", "summary"]:
                    with bench.lock:
                        self.respond(bench.run.summary())
                elif len(parts) >= 3 and parts[0] == "api" and parts[2] in bench.items:
                    action, sid = parts[1:3]
                    if action == "record":
                        with bench.lock:
                            self.respond(bench.record(sid))
                    elif action == "video":
                        with bench.lock:
                            path = playback(
                                bench.root, bench.items[sid], bench.maps[sid], bench.run
                            )
                        self.media(path)
                    elif action == "frame" and len(parts) == 4:
                        index = int(parts[3])
                        require(0 <= index < len(bench.maps[sid]["frames"]), "Invalid frame")
                        self.media(
                            relative(bench.root, bench.maps[sid]["frames"][index]["image_path"])
                        )
                    else:
                        self.respond({"error": "Unknown route"}, 404)
                else:
                    self.respond({"error": "Unknown route"}, 404)
            except ConnectionError:
                # Browsers cancel media requests when switching videos or seeking.
                # The connection is gone; do not attempt a second HTTP response.
                self.close_connection = True
            except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
                self.respond({"error": str(error)}, 400)

        def do_POST(self):
            if not self.allowed() or self.headers.get("X-Workbench-Token") != bench.token:
                self.respond({"error": "Local review token required"}, 403)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.respond({"error": "JSON required"}, 415)
                return
            try:
                parts = urlparse(self.path).path.strip("/").split("/")
                if parts == ["api", "control", "stop"]:
                    self.respond({"status": "STOPPING"})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                require(
                    len(parts) == 3 and parts[0] == "api" and parts[2] in bench.items,
                    "Unknown route",
                )
                length = int(self.headers.get("Content-Length", 0))
                require(0 < length <= 4 * 1024 * 1024, "Invalid request size")
                value = json.loads(self.rfile.read(length))
                self.respond(bench.write(parts[1], parts[2], value))
            except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
                self.respond({"error": str(error)}, 400)

    class ReviewServer(ThreadingHTTPServer):
        def server_close(self):
            with bench.lock:
                for task_id in list(bench.run.marks):
                    event = latest_task(bench.run, task_id)
                    if event and event["state"] in {"ACTIVE", "PAUSED"}:
                        bench.run.transition(task_id, "INTERRUPT", reason="SERVER_STOP")
            super().server_close()

    server = loopback_server(ReviewServer, Handler, port)
    server.workbench = bench
    return server
