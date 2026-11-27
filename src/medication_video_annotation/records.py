"""Identity linkage and immutable revisions. No automatic exports or blank saves."""

import copy
import json
import math
import re
from pathlib import Path

from .behavior_profile import legacy_label, profile_of, validate_behavior
from .io import (
    atomic_json,
    digest_bytes,
    file_hash,
    identifier,
    now,
    read,
    relative,
    require,
    writer_lock,
)
from .legacy_record import ANALYSIS_FIELDS, CHECKS, observed_indices, validate_record

TEMPLATE = Path(__file__).resolve().parents[2] / "templates/revision.template.json"


def record_frame_map_ref(item):
    """Imported references are opaque aliases, never filesystem paths to follow."""
    return item.get("imported_revision", {}).get("frame_map_ref", item["frame_map_ref"])


def revision_paths(root, item):
    directory = relative(root, item["record_dir"])
    paths = sorted(directory.glob("revision-*.json"))
    baseline = item.get("imported_revision")
    start = 0
    if baseline:
        name = baseline["filename"]
        require(re.fullmatch(r"revision-\d{6}\.json", name), "Invalid imported revision name")
        start = int(name[9:15])
        require(paths and paths[0].name == name, "Imported baseline missing")
        require(file_hash(paths[0]) == baseline["sha256"], "Imported baseline bytes changed")
    require(
        [p.name for p in paths]
        == [f"revision-{i:06d}.json" for i in range(start, start + len(paths))],
        "Revision sequence has gaps/invalid names; investigate before writing",
    )
    return paths


def record_hash(record):
    # Retain the legacy canonical record fingerprint (different from file SHA256).
    return digest_bytes(
        json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    )


def instantiate(video_id, source_sha256, frame_map_ref):
    identifier(video_id)
    record = read(TEMPLATE)
    record["video_id"] = record["label"]["video_id"] = video_id
    record["label"].update(
        source_ref=video_id, source_sha256=source_sha256, frame_map_ref=frame_map_ref
    )
    return record  # Memory only; remains invalid for saving until observed/completed.


def validate_link(root, item, frame_map, *, verify_files=False, map_snapshot_path=None):
    sid = identifier(item["video_id"])
    require(item["format"] == "onc110-dataset-index-entry-v0.1", "Invalid index version")
    require(frame_map["format"] == "onc110-frame-map-v0.1", "Invalid map version")
    require(frame_map["video_id"] == sid, "Frame map video identity mismatch")
    require(re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]), "Invalid source SHA256")
    require(frame_map["source_sha256"] == item["source_sha256"], "Source hash mismatch")
    require(frame_map["source_path"] == item["source_path"], "Source path mismatch")
    source = relative(root, item["source_path"])
    mapping = relative(root, item["frame_map_ref"])
    # Explicit handoff snapshots retain the original logical paths/aliases. Only
    # the bytes checked here may come from an already confined snapshot path.
    map_file = Path(map_snapshot_path) if map_snapshot_path is not None else mapping
    imported = item.get("imported_revision")
    if imported:
        require(imported["format"] == "onc110-imported-revision-v0.1", "Invalid import binding")
        require(
            imported["frame_map_ref"] == f"frame-map.private.json#samples/{sid}",
            "Invalid imported frame-map alias",
        )
        require(file_hash(map_file) == imported["frame_map_sha256"], "Imported map bytes changed")
        require(frame_map["generation"] == imported["generation"], "Imported generation mismatch")
    require(item["record_dir"] == f"records/{sid}", "Record directory must use stable ID")
    relative(root, item["record_dir"])
    require(source.name == item["original_filename"], "Original filename changed")
    classification = item["classification"]
    for key in ("tier", "behavior"):
        require(
            isinstance(classification[key], str)
            and classification[key]
            and "/" not in classification[key]
            and "\\" not in classification[key],
            "Classification must retain one directory component",
        )
    base = f"data/{classification['tier']}/{classification['behavior']}"
    require(
        item["source_path"] == f"{base}/videos/{item['original_filename']}",
        "Source path must preserve classification",
    )
    require(
        item["frame_map_ref"] == f"{base}/frames/{source.stem}/frame-map.json",
        "Frame directory must preserve original filename stem",
    )
    require(
        isinstance(item["source_lineage"], dict) and item["source_lineage"],
        "Source lineage required; unknown values remain explicit",
    )
    frames = frame_map["frames"]
    require(frames and frame_map["frame_count"] == len(frames), "Empty/mismatched frame map")
    require(
        all(type(frame_map[k]) is int and frame_map[k] > 0 for k in ("width", "height")),
        "Native dimensions required",
    )
    require(
        frame_map["generation"]["sampling"] == "EVERY_SOURCE_FRAME"
        and frame_map["generation"]["resize"] is False
        and frame_map["generation"].get("decoder_version")
        and frame_map["generation"].get("tool_version"),
        "Generation conditions required",
    )
    origin = frames[0]["source_ms"]
    require(frame_map["first_source_ms"] == origin, "PTS origin mismatch")
    previous = None
    images = set()
    for i, frame in enumerate(frames):
        require(
            type(frame["frame_index"]) is int and frame["frame_index"] == i,
            "Frame indices must be contiguous from zero",
        )
        pts, elapsed = frame["source_ms"], frame["player_elapsed_ms"]
        require(
            all(type(v) in (int, float) and math.isfinite(v) for v in (pts, elapsed)),
            "Finite timestamps required",
        )
        require(previous is None or pts > previous, "PTS must increase; duplicates need review")
        require(abs(elapsed - (pts - origin)) < 1e-6, "Player elapsed/PTS mismatch")
        image = relative(root, frame["image_path"])
        require(
            image.parent == mapping.parent
            and image.name == (f"frame-{i:06d}.png" if imported else f"frame_{i:06d}.png"),
            "Frame path/index mismatch",
        )
        require(frame["image_path"] not in images, "Duplicate frame path")
        images.add(frame["image_path"])
        if verify_files:
            require(image.is_file(), "Missing frame image")
        previous = pts
    if verify_files:
        require(file_hash(source) == item["source_sha256"], "Source bytes changed")
        require(read(map_file) == frame_map, "Stored frame map differs")
    return {**frame_map, "sample_id": sid}


def load_index(root, index_ref="manifests/dataset-index.jsonl"):
    items, identities, hashes, paths = [], set(), set(), set()
    for line in relative(root, index_ref).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        validate_link(root, item, read(relative(root, item["frame_map_ref"])))
        require(
            item["video_id"] not in identities and item["source_sha256"] not in hashes,
            "Duplicate video ID/content; retain alternate paths in source_lineage",
        )
        for path in (item["source_path"], item["frame_map_ref"], item["record_dir"]):
            require(path.casefold() not in paths, "Path collision (including Windows case)")
            paths.add(path.casefold())
        identities.add(item["video_id"])
        hashes.add(item["source_sha256"])
        items.append(item)
    return items


def validate_draft(record, sample, frame_map_ref):
    require("{{" not in json.dumps(record, ensure_ascii=False), "Unfilled placeholder")
    require(set(read(TEMPLATE)) <= set(record), "Missing legacy top-level field")
    require(isinstance(record["revision_history"], list), "Invalid revision history")
    require(record["label"]["frame_map_ref"] == frame_map_ref, "Frame map reference mismatch")
    profile = profile_of(record)
    # v0.1 checks still run: under a profile the old label is blank or frozen history.
    validate_record(record, sample)
    require(record["analysis"]["status"] in {"DRAFT", "REVIEWED"}, "Empty analysis cannot be saved")
    require(
        all(
            record["analysis"]["categories"][k]["status"] in {"DRAFT", "REVIEWED"}
            and record["analysis"]["categories"][k]["observations"]
            for k in ANALYSIS_FIELDS
        ),
        "All eight analysis categories need observations; incomplete work is a checkpoint",
    )
    require(
        observed_indices(record, sample) == set(range(len(sample["frames"]))),
        "First valid draft requires full observation",
    )
    if profile is not None:
        # The v0.1 label is blank or read-only history; its questions are not required.
        validate_behavior(record, sample, complete=True)
        return record
    coverage = record["label"]["coverage"]
    require(
        coverage["annotation_status"] in {"DRAFT", "REVIEWED"}
        and coverage["exhaustive"]
        and set(coverage["event_types"]) == set(CHECKS),
        "Draft coverage incomplete",
    )
    require(
        all(
            e["annotation_status"] in {"DRAFT", "REVIEWED"}
            and None not in e["check_answers"].values()
            for e in record["label"]["events"]
        ),
        "Incomplete event answers belong in checkpoint",
    )
    if not record["label"]["events"]:
        require(
            record["label"]["observations"], "Zero events needs absence/limitation observations"
        )
    return record


def content(record):
    return {k: v for k, v in record.items() if k not in {"measurements", "revision_history"}}


def preserve_fields(old, new):
    """Reject silent field loss; array item removal remains an explicit content edit."""
    if isinstance(old, dict) and isinstance(new, dict):
        require(set(old) <= set(new), "Preserve existing fields including nested extensions")
        for key in old:
            preserve_fields(old[key], new[key])
    elif isinstance(old, list) and isinstance(new, list):
        # Events have stable IDs; don't match shifted/deleted list elements by position.
        by_id = {v["event_id"]: v for v in new if isinstance(v, dict) and "event_id" in v}
        for value in old:
            if isinstance(value, dict) and value.get("event_id") in by_id:
                preserve_fields(value, by_id[value["event_id"]])


def check_profile_transition(root, item, old, old_path, record):
    """Old v0.1 meaning stays frozen; the active profile cites its exact base revision."""
    profile = profile_of(record)
    before = profile_of(old) if old else None
    if profile is None:
        require(before is None, "Cannot drop the active semantic profile")
        return
    block = record["label"]["behavior_annotation"]
    raw = block["authoring"]["raw_output"]
    if raw["status"] == "CAPTURED":
        require(
            raw["ref"].startswith("runs/") and file_hash(relative(root, raw["ref"])) == raw["sha256"],
            "Captured raw output must exist under runs/ with the recorded SHA256",
        )
    if old is None:
        blank = instantiate(item["video_id"], item["source_sha256"], record_frame_map_ref(item))
        require(
            legacy_label(record) == blank["label"] and block["basis"]["base_revision"] is None,
            "New-profile first draft cannot carry v0.1 answers or a base revision",
        )
        return
    require(
        legacy_label(record) == legacy_label(old),
        "v0.1 label history is read-only under the semantic profile",
    )
    if before is None:
        require(
            block["basis"]["base_revision"]
            == {
                "revision_id": old_path.name,
                "record_sha256": record_hash(old),
                "revision_file_sha256": file_hash(old_path),
            },
            "Semantic profile must cite the exact base revision",
        )
        require(
            not any(e in old["analysis"]["observation_log"] for e in block["observation_log"]),
            "Earlier observation log cannot be reused as new-profile observation",
        )
    else:
        old_block = old["label"]["behavior_annotation"]
        for key in ("profile", "guideline_ref", "basis"):
            require(block[key] == old_block[key], "Protected profile field changed: " + key)


def save_revision(root, item, frame_map, record, *, base_hash, actor):
    """Caller supplies whole record, including unknown fields. Returns unchanged on no-op."""
    require(isinstance(actor, str) and actor.strip(), "Actor required")
    sample = validate_link(root, item, frame_map, verify_files=True)
    validate_draft(record, sample, record_frame_map_ref(item))
    directory = relative(root, item["record_dir"])
    if profile_of(record) is not None:
        # Read-only pre-check so a refused profile draft leaves no record directory.
        paths = revision_paths(root, item)
        check_profile_transition(
            root, item, read(paths[-1]) if paths else None, paths[-1] if paths else None, record
        )
    # No record directory is created until a valid draft has passed validation.
    with writer_lock(directory):
        paths = revision_paths(root, item)
        old = read(paths[-1]) if paths else None
        require(base_hash == (record_hash(old) if old else None), "Record changed; reload")
        require(
            record["revision_history"] == (old["revision_history"] if old else []),
            "Client cannot alter authoritative revision history",
        )
        check_profile_transition(root, item, old, paths[-1] if paths else None, record)
        if old:
            preserve_fields(old, record)
            if content(old) == content(record):
                return {
                    "status": "UNCHANGED",
                    "path": str(paths[-1]),
                    "record": old,
                    "base_hash": record_hash(old),
                }
        result = copy.deepcopy(record)
        # Detailed new timing is stored in the run ledger, never copied into revisions.
        result["measurements"] = (
            copy.deepcopy(old["measurements"]) if old else record["measurements"]
        )
        result["revision_history"].append(
            {
                "at_kst": now(),
                "actor_ref": actor,
                "operation": "FIRST_VALID_DRAFT"
                if old is None
                else (
                    "SEMANTIC_PROFILE_START"
                    if profile_of(old) != profile_of(record)
                    else "CONTENT_UPDATE"
                ),
                "previous_sha256": base_hash,
            }
        )
        number = int(paths[-1].stem[9:]) + 1 if paths else 0
        target = directory / f"revision-{number:06d}.json"
        atomic_json(target, result, immutable=True)
        return {
            "status": "CREATED",
            "path": str(target),
            "record": result,
            "base_hash": record_hash(result),
        }
