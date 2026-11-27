"""Small synthetic VFR fixtures; never read or modify real video/label data."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_semantic_profile import e02_only, vfr

from medication_video_annotation.dev_handoff import (
    FORMAT,
    REQUEST,
    SELECTION_FORMAT,
    publish_dev_handoff,
    read_dev_handoff,
)
from medication_video_annotation.io import atomic_json, file_hash, read
from medication_video_annotation.records import record_hash, save_revision
from medication_video_annotation.workflow import save_with_review


@pytest.fixture
def selected(tmp_path):
    root = tmp_path / "합성 DEV"
    item, mapping = vfr(root)
    draft = save_revision(root, item, mapping, e02_only(item, mapping),
                          base_hash=None, actor="synthetic-author")
    saved = save_with_review(
        root, item, mapping, draft["record"], base_hash=draft["base_hash"],
        actor="synthetic-proxy", review_status="REVIEWED", reviewer_ref="synthetic-user",
        reviewed_ranges=[{"start_frame_index": 0, "end_frame_index": 11}],
    )
    index = root / "manifests/dataset-index.jsonl"
    index.parent.mkdir()
    index.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence = root / "runs/synthetic/user-review.json"
    atomic_json(evidence, {
        "reviewer_ref": "synthetic-user", "user_content_acceptance": "Synthetic acceptance",
        "user_answer": "Synthetic explicit full CLIP confirmation",
        "allowed_ranges": [{"video_id": item["video_id"], "start_frame_index": 0,
                            "end_frame_index": 11}],
        "actual_review_at_kst": None, "actual_review_duration_seconds": None,
    })
    selection = {
        "format": SELECTION_FORMAT, "handoff_id": "synthetic-dev-1",
        "entries": [{"video_id": item["video_id"], "revision_id": Path(saved["path"]).name,
                     "revision_file_sha256": file_hash(saved["path"]),
                     "frame_map_sha256": file_hash(root / item["frame_map_ref"]),
                     "groups": {k: {"status": "UNKNOWN", "value": None, "basis": "Synthetic"}
                                for k in ("person", "capture", "derived")}}],
        "user_evidence": {"path": "runs/synthetic/user-review.json", "sha256": file_hash(evidence),
                          "bytes": evidence.stat().st_size},
        "source_tools": {kind: {"synthetic.py": "a" * 64} for kind in ("authoring", "review")},
    }
    return root, item, mapping, saved, selection


def publish(fixture):
    root, _, _, _, selection = fixture
    return publish_dev_handoff(root, selection, request=REQUEST)


def check(result, root, ids=None):
    return read_dev_handoff(result["path"], media_root=root,
                            expected_manifest_sha256=result["manifest_sha256"], expected_ids=ids)


def reseal(result, manifest):
    directory = Path(result["path"])
    atomic_json(directory / "manifest.json", manifest)
    digest = file_hash(directory / "manifest.json")
    (directory / "manifest.sha256").write_bytes((digest + "  manifest.json\n").encode())
    result["manifest_sha256"] = digest


def rewrite_snapshot(result, key, mutate):
    manifest = read(result["manifest"])
    entry = manifest["entries"][0]
    snapshot = Path(result["path"]) / entry[key]["path"]
    value = read(snapshot)
    mutate(value)
    atomic_json(snapshot, value)
    entry[key].update(sha256=file_hash(snapshot), bytes=snapshot.stat().st_size)
    if key == "record":
        entry["canonical_record_sha256"] = record_hash(value)
    reseal(result, manifest)


def test_publish_read_preserves_bytes_vfr_and_ignores_latest(selected):
    root, item, mapping, saved, selection = selected
    result = publish(selected)
    checked = check(result, root, [item["video_id"]])
    assert checked["manifest"]["format"] == FORMAT
    entry = checked["manifest"]["entries"][0]
    assert (Path(result["path"]) / entry["record"]["path"]).read_bytes() == Path(saved["path"]).read_bytes()
    assert (Path(result["path"]) / entry["frame_map"]["path"]).read_bytes() == (root / item["frame_map_ref"]).read_bytes()
    assert checked["frame_maps"][0]["frames"][2]["source_ms"] == 1087.5
    assert checked["frame_maps"][0]["first_source_ms"] == 1000
    # Reader needs only fixed snapshots and the media tree, not live records/maps/index.
    Path(saved["path"]).write_text("unrelated later corruption", encoding="utf-8")
    (root / item["frame_map_ref"]).unlink()
    (root / "manifests/dataset-index.jsonl").unlink()
    assert check(result, root)["records"][0] == saved["record"]
    with pytest.raises(ValueError, match="IDs/order"):
        check(result, root, ["OTHER"])


def test_registered_import_alias_is_explicit_and_byte_preserved(selected):
    root, item, mapping, saved, selection = selected
    for frame in mapping["frames"]:
        before = root / frame["image_path"]
        frame["image_path"] = frame["image_path"].replace("frame_", "frame-")
        before.rename(root / frame["image_path"])
    atomic_json(root / item["frame_map_ref"], mapping)
    alias = "frame-map.private.json#samples/" + item["video_id"]
    item["imported_revision"] = {
        "format": "onc110-imported-revision-v0.1", "filename": "revision-000000.json",
        "sha256": file_hash(root / item["record_dir"] / "revision-000000.json"),
        "frame_map_ref": alias, "frame_map_sha256": file_hash(root / item["frame_map_ref"]),
        "generation": mapping["generation"],
    }
    saved["record"]["label"]["frame_map_ref"] = alias
    atomic_json(saved["path"], saved["record"])
    selection["entries"][0]["revision_file_sha256"] = file_hash(saved["path"])
    selection["entries"][0]["frame_map_sha256"] = file_hash(root / item["frame_map_ref"])
    (root / "manifests/dataset-index.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
    result = publish(selected)
    checked = check(result, root)
    assert checked["manifest"]["entries"][0]["map_alias"]["alias"] == alias
    assert checked["records"][0]["label"]["frame_map_ref"] == alias
    (root / item["frame_map_ref"]).unlink()
    assert check(result, root)["frame_maps"][0] == mapping
    manifest = checked["manifest"]
    manifest["entries"][0]["map_alias"]["alias"] = "unregistered"
    reseal(result, manifest)
    with pytest.raises(ValueError, match="alias"):
        check(result, root)


@pytest.mark.parametrize("case", ["draft", "wrong_hash", "map_pin", "duplicate", "latest", "wrong_id", "no_request"])
def test_publication_refusals_do_not_publish(selected, case):
    root, item, _, _, selection = selected
    if case == "draft":
        pick = selection["entries"][0]
        pick["revision_id"] = "revision-000000.json"
        pick["revision_file_sha256"] = file_hash(root / item["record_dir"] / pick["revision_id"])
    elif case == "wrong_hash":
        selection["entries"][0]["revision_file_sha256"] = "0" * 64
    elif case == "map_pin":
        selection["entries"][0]["frame_map_sha256"] = "0" * 64
    elif case == "duplicate":
        selection["entries"].append(copy.deepcopy(selection["entries"][0]))
    elif case == "latest":
        selection["entries"][0]["revision_id"] = "latest"
    elif case == "wrong_id":
        selection["entries"][0]["video_id"] = "MISSING"
    with pytest.raises(ValueError):
        publish_dev_handoff(root, selection, request="AUTO" if case == "no_request" else REQUEST)
    assert not (root / "handoffs" / selection["handoff_id"]).exists()


def test_overwrite_refused(selected):
    result = publish(selected)
    before = Path(result["manifest"]).read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        publish(selected)
    assert Path(result["manifest"]).read_bytes() == before


@pytest.mark.parametrize("case", [
    "manifest_hash", "sidecar", "record_hash", "missing", "version", "profile", "duplicate",
    "count", "entry_id", "map_id", "record_id", "pts", "range", "review", "annotation",
    "root_alias", "source_escape", "snapshot_escape", "absolute", "map_escape", "missing_frame",
    "source_hash", "extra_file", "evidence_range", "source_tools", "map_hash",
])
def test_reader_rejects_tamper_and_invalid_linkage(selected, case):
    root, item, mapping, _, _ = selected
    result = publish(selected)
    directory = Path(result["path"])
    manifest = read(result["manifest"])
    entry = manifest["entries"][0]
    if case == "manifest_hash":
        Path(result["manifest"]).write_bytes(Path(result["manifest"]).read_bytes() + b" ")
    elif case == "sidecar":
        (directory / "manifest.sha256").write_text("bad", encoding="ascii")
    elif case in {"record_hash", "missing"}:
        p = directory / entry["record"]["path"]
        p.unlink() if case == "missing" else p.write_bytes(p.read_bytes() + b" ")
    elif case in {"map_id", "map_escape", "pts"}:
        def change(m):
            if case == "map_id":
                m["video_id"] = "WRONG"
            elif case == "map_escape":
                m["frames"][0]["image_path"] = "../outside.png"
            else:
                m["frames"][2]["pts"] += 1
        rewrite_snapshot(result, "frame_map", change)
    elif case in {"record_id", "range", "review"}:
        def change(r):
            b = r["label"]["behavior_annotation"]
            if case == "record_id":
                r["video_id"] = "WRONG"
            elif case == "range":
                b["events"][0]["interval"]["end_last_included"]["frame_index"] = 999
            else:
                b["review"]["human_review"]["status"] = "NOT_RUN"
        rewrite_snapshot(result, "record", change)
    elif case == "missing_frame":
        (root / mapping["frames"][0]["image_path"]).unlink()
    elif case == "source_hash":
        (root / item["source_path"]).write_bytes(b"changed")
    elif case == "extra_file":
        (directory / "unexpected.csv").write_bytes(b"x")
    else:
        if case == "version":
            manifest["version"] = "2"
        elif case == "profile":
            manifest["profile"] = "unknown"
        elif case == "duplicate":
            manifest["entries"].append(copy.deepcopy(entry))
            manifest["selected_ids"].append(entry["video_id"])
            manifest["selected_count"] = 2
        elif case == "count":
            manifest["selected_count"] = 9
        elif case == "entry_id":
            entry["dataset_entry"]["video_id"] = "WRONG"
        elif case == "annotation":
            entry["annotation"]["scopes"][0]["status"] = "NOT_OBSERVED"
        elif case == "root_alias":
            entry["source"]["root_alias"] = "OTHER_ROOT"
        elif case == "source_escape":
            entry["source"]["locator"] = "../video.mp4"
        elif case == "snapshot_escape":
            entry["record"]["path"] = "../record.json"
        elif case == "absolute":
            entry["record"]["path"] = "C:/record.json"
        elif case == "map_hash":
            entry["frame_map"]["sha256"] = "b" * 64
        elif case == "source_tools":
            del manifest["source_tools"]["review"]
        elif case == "evidence_range":
            p = directory / manifest["user_evidence"]["path"]
            evidence = read(p)
            evidence["allowed_ranges"][0]["end_frame_index"] = 3
            atomic_json(p, evidence)
            manifest["user_evidence"].update(sha256=file_hash(p), bytes=p.stat().st_size)
        reseal(result, manifest)
    with pytest.raises(ValueError):
        check(result, root)


def test_cli_read_only_check(selected):
    root, item, _, _, _ = selected
    result = publish(selected)
    proc = subprocess.run([
        sys.executable, "-X", "utf8", "-m", "medication_video_annotation", "--data-root", str(root),
        "dev-handoff-check", "--handoff", result["path"], "--manifest-sha256",
        result["manifest_sha256"], "--expect-ids", item["video_id"],
    ], capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "PASS"


def test_frame_hash_and_root_binding_are_checked(selected, tmp_path):
    root, item, mapping, _, selection = selected
    for frame in mapping["frames"]:
        frame["image_sha256"] = file_hash(root / frame["image_path"])
    atomic_json(root / item["frame_map_ref"], mapping)
    selection["entries"][0]["frame_map_sha256"] = file_hash(root / item["frame_map_ref"])
    result = publish(selected)
    with pytest.raises(ValueError):
        check(result, tmp_path)
    (root / mapping["frames"][0]["image_path"]).write_bytes(b"changed frame")
    with pytest.raises(ValueError, match="Frame image hash"):
        check(result, root)


def test_symlink_escape_rejected(selected, tmp_path):
    root, _, _, _, _ = selected
    result = publish(selected)
    manifest = read(result["manifest"])
    entry = manifest["entries"][0]
    p = Path(result["path"]) / entry["record"]["path"]
    external = tmp_path / "outside-record.json"
    external.write_bytes(p.read_bytes())
    p.unlink()
    try:
        p.symlink_to(external)
    except OSError:
        pytest.skip("Host does not permit creating symlinks; lexical escape checks still run")
    with pytest.raises(ValueError, match="escapes"):
        check(result, root)
