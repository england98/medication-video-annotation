"""One reusable value-entry session; no model calls, new record format, or exports."""

import copy
import json
import sys

from .behavior_profile import profile_of
from .io import file_hash, relative, require, writer_lock
from .operations import latest_task
from .server import Workbench
from .timing import TERMINAL
from .workflow import begin_profile


class AuthorSession:
    def __init__(self, root, run_path, video_id, actor, profile=None):
        require(actor.strip(), "Actor required")
        self.bench = Workbench(root, run_path, [video_id])
        self.run, self.sid, self.actor = self.bench.run, video_id, actor
        self.task_id = f"ai-draft-{video_id}"
        self.profile = profile
        self.data = self.bench.record(video_id)
        self.record = self.start(self.data)
        self.cursor = 0

    def start(self, data):
        record, current = copy.deepcopy(data["record"]), data["semantic_profile"]
        if self.profile is None or current is not None:
            # A session never mixes meanings: the latest revision's profile must be requested.
            require(current == self.profile, f"Latest record uses profile {current!r}")
            return record
        revision = data["revision_id"]
        item = self.bench.items[self.sid]
        return begin_profile(
            record,
            item,
            profile=self.profile,
            revision_id=revision,
            revision_file_sha256=file_hash(relative(self.bench.root, f"{item['record_dir']}/{revision}"))
            if revision
            else None,
            actor=self.actor,
        )

    def dispatch(self, request):
        command = request["command"]
        if command == "get":
            return {
                "record": self.record,
                "semantic_profile": self.profile,
                "base_hash": self.data["base_hash"],
                "revision_id": self.data["revision_id"],
                "cursor": self.cursor,
                "checkpoint_hash": self.data["checkpoint_hash"],
                "checkpoint_available": self.data["checkpoint"] is not None,
                "checkpoint_stale": self.data["checkpoint_stale"],
                "frame_map_ref": self.bench.items[self.sid]["frame_map_ref"],
                "timing": latest_task(self.run, self.task_id),
            }
        if command == "put":
            candidate = copy.deepcopy(request["record"])
            require(candidate["video_id"] == self.sid, "Wrong video record")
            require(profile_of(candidate) == self.profile, "Record profile differs from session")
            self.record = candidate
            return {"status": "IN_MEMORY_ONLY"}
        if command == "set":
            path = request["path"]
            require(isinstance(path, list) and path, "Nonempty field path required")
            candidate = copy.deepcopy(self.record)
            target = candidate
            for part in path[:-1]:
                target = target[part]
            require(
                (isinstance(target, dict) and path[-1] in target)
                or (
                    isinstance(target, list)
                    and type(path[-1]) is int
                    and 0 <= path[-1] < len(target)
                ),
                "Existing field required",
            )
            target[path[-1]] = copy.deepcopy(request["value"])
            self.record = candidate
            return {"status": "IN_MEMORY_ONLY"}
        if command == "restore":
            current = self.bench.record(self.sid)
            require(
                current["checkpoint"] and not current["checkpoint_stale"],
                "No current checkpoint; do not apply stale values",
            )
            checkpoint = current["checkpoint"]
            require(
                checkpoint.get("semantic_profile") == self.profile
                and profile_of(checkpoint["record"]) == self.profile,
                "Checkpoint semantic profile differs from this session",
            )
            if self.profile:
                require(
                    checkpoint.get("base_revision_id") == current["revision_id"],
                    "Checkpoint base revision differs from the latest revision",
                )
            self.data = current
            self.record = copy.deepcopy(current["checkpoint"]["record"])
            self.cursor = current["checkpoint"]["cursor"]
            return self.dispatch({"command": "get"})
        if command == "timer":
            action = request["action"]
            old = latest_task(self.run, self.task_id)
            if action == "START":
                latest = {e["task_id"]: e for e in self.run.events() if e.get("task_id")}
                require(
                    not any(
                        e["state"] not in TERMINAL
                        and e["task_id"] != self.task_id
                        and e.get("activity_kind") in {"INTERACTIVE_SESSION", "HUMAN_SESSION"}
                        for e in latest.values()
                    ),
                    "Another observation/review session is open",
                )
                return self.run.start(
                    self.task_id,
                    "OBSERVATION_DRAFT",
                    video_id=self.sid,
                    activity_kind="INTERACTIVE_SESSION",
                    actor_ref=self.actor,
                    retry_reason=request.get("retry_reason"),
                )
            require(old and old.get("actor_ref") == self.actor, "Timer actor differs")
            return self.run.transition(
                self.task_id, action, reason=request.get("reason"), result=request.get("result")
            )
        if command in {"checkpoint", "save"}:
            if command == "save":
                event = latest_task(self.run, self.task_id)
                require(
                    event and event["state"] in {"PAUSED", "COMPLETE"},
                    "Pause/finish AI session before timed validation/save",
                )
            value = {
                "record": self.record,
                "base_hash": self.data["base_hash"],
                "actor_ref": self.actor,
            }
            if command == "checkpoint":
                value.update(
                    checkpoint_hash=self.data["checkpoint_hash"],
                    cursor=request["cursor"],
                    note=request.get("note", ""),
                    replace_reason=request.get("replace_reason"),
                )
            result = self.bench.write(command, self.sid, value)
            self.data = self.bench.record(self.sid)
            if command == "checkpoint":
                self.cursor = request["cursor"]
            else:
                self.record = copy.deepcopy(result["record"])
            return result
        raise ValueError("Unknown author command; review/export are not authoring operations")

    def close(self):
        if self.task_id in self.run.marks:
            self.run.transition(self.task_id, "INTERRUPT", reason="AUTHOR_SESSION_CLOSED")


def session(root, run_path, video_id, actor, profile=None):
    with writer_lock(root / "service"):
        author = AuthorSession(root, run_path, video_id, actor, profile)
        print(
            json.dumps(
                {"ok": True, "ready": True, "video_id": video_id, "semantic_profile": profile}
            ),
            flush=True,
        )
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    if request.get("command") == "quit":
                        break
                    result = author.dispatch(request)
                    print(
                        json.dumps(
                            {"ok": True, "value": result}, ensure_ascii=False, allow_nan=False
                        ),
                        flush=True,
                    )
                except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
                    print(
                        json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False),
                        flush=True,
                    )
        finally:
            author.close()
