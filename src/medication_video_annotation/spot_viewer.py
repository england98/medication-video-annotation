"""Read-only viewer for medication-event-spot-label-v1 drafts and their source videos."""

import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .io import file_hash, read, relative, require
from .server import LocalHandler, loopback_server

WEB = Path(__file__).resolve().parents[2] / "web/spot_viewer.html"
LABEL_FORMAT = "medication-event-spot-label-v1"
TAXONOMY_DIR = "data_curated/복약행동_분류"
BEHAVIOR_CODE = re.compile(r"(?:^|_)(A\d{3})(?=_)")
UNCLASSIFIED = {"name": "미분류", "group": "미분류", "group_folder": "~"}


def load_taxonomy(data_root):
    """Read action names from the existing classification folders (metadata, not a judgment)."""
    taxonomy = {}
    base = Path(data_root) / TAXONOMY_DIR
    if not base.is_dir():
        return taxonomy
    for group in sorted(p for p in base.iterdir() if p.is_dir()):
        for folder in sorted(p for p in group.iterdir() if p.is_dir()):
            match = re.fullmatch(r"(A\d{3})_(.+)", folder.name)
            if match:
                taxonomy[match[1]] = {
                    "name": match[2],
                    "group": re.sub(r"^\d+_", "", group.name),
                    "group_folder": group.name,
                }
    return taxonomy


def source_status(path, expected_sha256):
    if not path.is_file():
        return "MISSING"
    return "OK" if file_hash(path) == expected_sha256 else "HASH_MISMATCH"


def load_catalog(label_root, source_root=None):
    """Return the browser catalog and the verified source path of each playable video.

    `source_root` overrides the data collection root recorded in the latest event-spot run
    (useful after the source folders move or are restored elsewhere).
    """
    label_root = Path(label_root)
    summaries = sorted(
        (read(path) for path in label_root.glob("runs/*/summary.json")),
        key=lambda summary: summary["started_at_kst"],
    )
    require(summaries, f"No event-spot run summary under {label_root}")
    data_root = Path(source_root or summaries[-1]["data_root"])
    require(data_root.is_dir(), f"Source root not found: {data_root}; pass --source-root")
    data_root = data_root.resolve()
    purposes = {e["video_id"]: e["batch_purpose"] for s in summaries for e in s["entries"]}
    taxonomy = load_taxonomy(data_root)
    videos, paths, behaviors = [], {}, {}
    for folder in sorted(label_root.glob("records/*")):
        revisions = sorted(folder.glob("revision-*.json"))
        if not revisions:
            continue
        record = read(revisions[-1])
        require(record.get("format") == LABEL_FORMAT, f"Unsupported label format: {revisions[-1]}")
        require(record["video_id"] == folder.name, f"Label ID differs from folder: {revisions[-1]}")
        source = relative(data_root, record["source"]["locator"])
        status = source_status(source, record["source"]["sha256"])
        if status == "OK":
            paths[record["video_id"]] = source
        match = BEHAVIOR_CODE.search(source.name)
        code = match[1] if match else "미분류"
        behavior = {"code": code, **taxonomy.get(code, UNCLASSIFIED)}
        behaviors.setdefault(code, {**behavior, "count": 0})["count"] += 1
        videos.append(
            {
                "video_id": record["video_id"],
                "filename": source.name,
                "behavior_code": code,
                "batch_purpose": purposes.get(record["video_id"]),
                "source_status": status,
                "revision_file": revisions[-1].name,
                "record": record,
            }
        )
    videos.sort(key=lambda video: video["filename"])
    ordered = sorted(behaviors.values(), key=lambda b: (b["group_folder"], b["code"]))
    return {"behaviors": ordered, "videos": videos}, paths


def make_viewer_server(label_root, port=8769, source_root=None):
    state = {"loaded": load_catalog(label_root, source_root)}

    class Handler(LocalHandler):
        def log_message(self, *_):
            pass  # Range requests would otherwise flood the launch terminal.

        def do_GET(self):
            if not self.allowed():
                self.respond({"error": "Local same-origin access only"}, 403)
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                if parts == [""]:
                    self.respond(WEB.read_bytes(), content_type="text/html; charset=utf-8")
                elif parts == ["api", "catalog"]:
                    state["loaded"] = load_catalog(label_root, source_root)
                    self.respond(state["loaded"][0])
                elif len(parts) == 3 and parts[:2] == ["api", "video"]:
                    source = state["loaded"][1].get(parts[2])
                    if source is None:
                        self.respond({"error": "Video is not available"}, 404)
                    else:
                        self.media(source)
                else:
                    self.respond({"error": "Unknown route"}, 404)
            except ConnectionError:
                self.close_connection = True  # The browser cancelled a media request.
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.respond({"error": str(error)}, 400)

    return loopback_server(ThreadingHTTPServer, Handler, port)
