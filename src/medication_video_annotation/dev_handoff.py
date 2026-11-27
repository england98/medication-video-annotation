"""Explicit, immutable, data-side DEV handoffs. Not the v0.1 export/release contract."""

import copy
import os
import re
import uuid
from pathlib import Path

from .behavior_profile import PROFILE, human_reviewed, profile_of
from .io import (
    digest_bytes,
    encoded,
    file_hash,
    identifier,
    now,
    read,
    relative,
    require,
    timestamp,
    writer_lock,
)
from .records import load_index, record_frame_map_ref, record_hash, validate_draft, validate_link

FORMAT = "onc110-behavior-dev-handoff-v0.1"
SELECTION_FORMAT = "onc110-behavior-dev-selection-v0.1"
REQUEST = "EXPLICIT_USER_DEV_HANDOFF"
ASSET_ROOTS = {"DATA_ROOT": {"binding": "CALLER_SUPPLIED", "access": "READ_ONLY"}}
POLICY = {
    "purpose": "DEV",
    "development_exposure": "DEV_EXPOSED",
    "final_ground_truth": False,
    "evaluation_eligible": False,
    "data_ledger_access": "READ_ONLY",
    "updates": "NEW_REVISION_AND_NEW_HANDOFF_ID",
    "pipeline_contract": "DATA_SIDE_ONLY_NOT_ADOPTED",
}
TIME_AXIS = {
    "frame_index": "ZERO_BASED_SOURCE_FRAME",
    "pts": "ORIGINAL_INTEGER",
    "time_base": "ORIGINAL_RATIONAL_SECONDS",
    "source_ms": "ORIGINAL_PTS_MILLISECONDS_NO_ROUNDING",
    "player_elapsed_ms": "SOURCE_MS_MINUS_FIRST_SOURCE_MS",
    "end": "LAST_INCLUDED_FRAME",
    "integer_ms_adapter": "NOT_IMPLEMENTED",
}


def _sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "Invalid SHA256")
    return value


def _revision(value):
    require(isinstance(value, str) and re.fullmatch(r"revision-\d{6}\.json", value),
            "Explicit numbered revision required; latest is not supported")
    return value


def _text(value, what):
    require(isinstance(value, str) and value.strip(), what + " required")


def _file_info(path, locator):
    return {"path": locator, "sha256": file_hash(path), "bytes": Path(path).stat().st_size}


def _checked_file(root, info, expected_path=None):
    path = relative(root, info["path"])
    if expected_path is not None:
        require(info["path"] == expected_path, "Unexpected snapshot path")
    require(type(info["bytes"]) is int and info["bytes"] > 0, "Invalid snapshot byte size")
    require(path.is_file(), "Missing snapshot: " + info["path"])
    require(path.stat().st_size == info["bytes"] and file_hash(path) == _sha(info["sha256"]),
            "Snapshot bytes/hash mismatch: " + info["path"])
    return path


def _tool_fingerprint():
    base = Path(__file__).resolve().parent
    files = {p.name: file_hash(p) for p in sorted(base.glob("*.py"))}
    return {"name": "medication-video-annotation", "version": "0.1.0",
            "module": "dev_handoff", "source_files_sha256": files,
            "source_tree_sha256": digest_bytes(encoded(files))}


def _groups(groups):
    require(isinstance(groups, dict) and set(groups) == {"person", "capture", "derived"},
            "Explicit person/capture/derived groups required")
    for group in groups.values():
        require(group["status"] in {"METADATA_CODE", "CANDIDATE", "UNKNOWN"},
                "Invalid group evidence status")
        if group["status"] == "UNKNOWN":
            require(group["value"] is None, "Unknown group must remain null")
        else:
            _text(group["value"], "Group value")
        _text(group["basis"], "Group evidence/limitation")


def _summary(record, mapping):
    block = record["label"]["behavior_annotation"]
    return {
        "source_actor_ids": [a["source_actor_id"] for a in block["actors"]],
        "observed_range": {"start_frame_index": 0, "end_frame_index": mapping["frame_count"] - 1},
        "scopes": [{k: s[k] for k in ("assessment_id", "scope", "source_actor_id", "range",
                                     "status")} for s in block["scope_assessments"]],
        "record_pointer": "/label/behavior_annotation",
        "review": copy.deepcopy(block["review"]),
        "authoring": copy.deepcopy(block["authoring"]),
        "basis": copy.deepcopy(block["basis"]),
        "guideline_ref": copy.deepcopy(block["guideline_ref"]),
    }


def _reviewed(record, mapping):
    require(profile_of(record) == PROFILE, "Unsupported DEV handoff profile")
    require(human_reviewed(record) and record["analysis"]["status"] == "REVIEWED",
            "DEV handoff requires a saved human-reviewed revision")
    require(all(c["status"] == "REVIEWED" for c in record["analysis"]["categories"].values()),
            "Analysis review incomplete")
    block = record["label"]["behavior_annotation"]
    human = block["review"]["human_review"]
    require(human["reviewed_ranges"] == [{"start_frame_index": 0,
                                         "end_frame_index": mapping["frame_count"] - 1}],
            "Full CLIP review range required")
    _text(human["reviewer_ref"], "Human reviewer")
    timestamp(human["reviewed_at_kst"])
    require(human["history"] and any(h.get("operation") == "HUMAN_REVIEW_COMPLETE"
                                    for h in human["history"]), "Saved review history required")
    for container in (record["label"], block):
        require(container["final_ground_truth"] is False
                and container["evaluation_eligible"] is False, "DEV is not evaluation truth")
    require(block["review"]["independent_review"] == "NOT_RUN"
            and block["review"]["release_freeze"] == "NOT_RUN", "DEV cannot promote release/test")


def _evidence(evidence, entries):
    _text(evidence["reviewer_ref"], "Evidence reviewer")
    _text(evidence["user_content_acceptance"], "User content acceptance")
    _text(evidence["user_answer"], "Explicit user range confirmation")
    ranges = evidence["allowed_ranges"]
    require(isinstance(ranges, list), "Evidence range list required")
    expected = [{"video_id": e["video_id"], **e["annotation"]["observed_range"]}
                for e in entries]
    require(ranges == expected, "User evidence differs from selected ordered full CLIP ranges")
    require(all(e["annotation"]["review"]["human_review"]["reviewer_ref"]
                == evidence["reviewer_ref"] for e in entries), "Evidence reviewer mismatch")


def _read_handoff(directory, media_root, expected_manifest_sha256, expected_ids):
    directory, media_root = Path(directory).resolve(strict=True), Path(media_root).resolve(strict=True)
    manifest_path = relative(directory, "manifest.json")
    digest = file_hash(manifest_path)
    require(digest == _sha(expected_manifest_sha256), "Pinned manifest SHA256 mismatch")
    require(relative(directory, "manifest.sha256").read_text(encoding="ascii")
            == digest + "  manifest.json\n", "Manifest sidecar mismatch")
    manifest = read(manifest_path)
    require(manifest["format"] == FORMAT and manifest["version"] == "0.1",
            "Unsupported DEV handoff format/version")
    identifier(manifest["handoff_id"])
    timestamp(manifest["created_at_kst"])
    require(manifest["profile"] == PROFILE, "Unsupported handoff profile")
    require(encoded(manifest["policy"]) == encoded(POLICY) and manifest["purpose"] == "DEV",
            "Invalid DEV policy")
    require(manifest["asset_roots"] == ASSET_ROOTS and manifest["time_axis"] == TIME_AXIS,
            "Unsupported root/time semantics")
    tooling = manifest["publisher"]
    require(tooling["name"] == "medication-video-annotation" and tooling["module"] == "dev_handoff"
            and tooling["version"] == "0.1.0", "Unsupported publisher descriptor")
    hashes = tooling["source_files_sha256"]
    require(isinstance(hashes, dict) and "dev_handoff.py" in hashes, "Publisher code hashes required")
    for name, value in hashes.items():
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name), "Invalid tool source name")
        _sha(value)
    require(digest_bytes(encoded(hashes)) == _sha(tooling["source_tree_sha256"]),
            "Publisher fingerprint mismatch")
    source_tools = manifest["source_tools"]
    for kind in ("authoring", "review"):
        require(isinstance(source_tools[kind], dict) and source_tools[kind], "Source tool hashes required")
        for name, value in source_tools[kind].items():
            relative(media_root, name)  # Descriptor only; never execute/load code from this path.
            _sha(value)
    entries, ids = manifest["entries"], manifest["selected_ids"]
    require(isinstance(entries, list) and entries and isinstance(ids, list), "Empty DEV handoff")
    require(type(manifest["selected_count"]) is int
            and manifest["selected_count"] == len(entries) == len(ids), "Selected count mismatch")
    require(len({identifier(s).casefold() for s in ids}) == len(ids)
            and ids == [e["video_id"] for e in entries],
            "Duplicate/mismatched ordered IDs")
    if expected_ids is not None:
        require(ids == list(expected_ids), "Pinned selected IDs/order mismatch")
    paths = {"manifest.json", "manifest.sha256", "README.txt", "evidence/user-review.json"}
    records, maps, sources = [], [], set()
    for entry in entries:
        sid = identifier(entry["video_id"])
        item = entry["dataset_entry"]
        require(item["video_id"] == sid, "Dataset identity mismatch")
        rid = _revision(entry["revision_id"])
        require(entry["revision_ref"] == f"records/{sid}/{rid}", "Original revision reference mismatch")
        relative(media_root, entry["revision_ref"])  # Never follow latest or original revision bytes.
        record_path = _checked_file(directory, entry["record"], f"records/{sid}/{rid}")
        map_path = _checked_file(directory, entry["frame_map"], f"frame-maps/{sid}.json")
        paths.update((entry["record"]["path"], entry["frame_map"]["path"]))
        record, mapping = read(record_path), read(map_path)
        require(entry["frame_map_ref"] == item["frame_map_ref"], "Map locator mismatch")
        require(entry["record_frame_map_ref"] == record_frame_map_ref(item), "Map alias mismatch")
        require(entry["map_alias"] == (
            {"alias": record_frame_map_ref(item), "target": item["frame_map_ref"],
             "basis": "dataset_entry.imported_revision"} if item.get("imported_revision") else None
        ), "Undeclared or invalid map alias")
        # The shared validator checks exact ID, registered import alias, generation,
        # source path/hash, frame locations and rational PTS without modifying the map.
        sample = validate_link(media_root, item, mapping, verify_files=True, map_snapshot_path=map_path)
        validate_draft(record, sample, record_frame_map_ref(item))
        for frame in mapping["frames"]:
            if "image_sha256" in frame:
                require(file_hash(relative(media_root, frame["image_path"]))
                        == _sha(frame["image_sha256"]), "Frame image hash mismatch")
        _reviewed(record, mapping)
        require(record["video_id"] == sid and record["label"]["source_ref"] == sid,
                "Record source identity mismatch")
        require(entry["record_format"] == record["format"]
                and entry["label_format"] == record["label"]["format"]
                and entry["profile"] == PROFILE, "Record format/profile mismatch")
        require(entry["canonical_record_sha256"] == record_hash(record), "Canonical record hash mismatch")
        source = entry["source"]
        source_path = relative(media_root, source["locator"])
        require(source["root_alias"] == "DATA_ROOT" and source["locator"] == item["source_path"]
                and source["sha256"] == item["source_sha256"]
                and type(source["bytes"]) is int and source_path.stat().st_size == source["bytes"],
                "Source locator/hash/size mismatch")
        require(source["sha256"] not in sources, "Duplicate source content")
        sources.add(source["sha256"])
        require(entry["map_summary"] == {k: mapping[k] for k in
                ("video_id", "source_sha256", "frame_count", "width", "height", "first_source_ms")},
                "Map summary mismatch")
        require(entry["annotation"] == _summary(record, mapping), "Annotation/review provenance mismatch")
        require(entry["development_exposure"] == "DEV_EXPOSED", "DEV exposure required")
        _groups(entry["groups"])
        records.append(record)
        maps.append(mapping)
    evidence_path = _checked_file(directory, manifest["user_evidence"], "evidence/user-review.json")
    _evidence(read(evidence_path), entries)
    _checked_file(directory, manifest["instructions"], "README.txt")
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    require(actual == paths, "Unexpected/missing handoff files")
    return {"manifest": manifest, "manifest_sha256": digest, "records": records, "frame_maps": maps}


def read_dev_handoff(directory, *, media_root, expected_manifest_sha256, expected_ids=None):
    """Read fixed snapshots and validate media binding; does not write a data ledger."""
    try:
        return _read_handoff(directory, media_root, expected_manifest_sha256, expected_ids)
    except (KeyError, TypeError, OSError) as error:
        raise ValueError(f"Invalid/incomplete DEV handoff: {error}") from error


def publish_dev_handoff(root, selection, *, request):
    """Publish only explicitly selected reviewed file hashes, via validated staging."""
    require(request == REQUEST, "Explicit user DEV publication request required")
    require(selection["format"] == SELECTION_FORMAT, "Unsupported selection format")
    root = Path(root).resolve(strict=True)
    handoff_id = identifier(selection["handoff_id"])
    picks = selection["entries"]
    require(isinstance(picks, list) and picks, "Explicit revision selection required")
    ids = [identifier(p["video_id"]) for p in picks]
    require(len(ids) == len(set(ids)), "Duplicate selected IDs")
    items = {i["video_id"]: i for i in load_index(root)}
    require(set(ids) <= set(items), "Unknown selected ID")
    directory = relative(root, "handoffs")
    target = relative(directory, handoff_id)
    with writer_lock(directory):
        require(not target.exists(), "Handoff already exists; use a new handoff_id")
        pending = directory / (".pending-" + uuid.uuid4().hex)
        pending.mkdir()
        entries = []
        for pick in picks:
            sid, rid = pick["video_id"], _revision(pick["revision_id"])
            item = items[sid]
            revision_ref = f"{item['record_dir']}/{rid}"
            source_record = relative(root, revision_ref)
            require(file_hash(source_record) == _sha(pick["revision_file_sha256"]),
                    "Selected revision file hash mismatch")
            map_source = relative(root, item["frame_map_ref"])
            require(file_hash(map_source) == _sha(pick["frame_map_sha256"]),
                    "Selected frame-map file hash mismatch")
            record_rel, map_rel = f"records/{sid}/{rid}", f"frame-maps/{sid}.json"
            for source, dest in ((source_record, record_rel), (map_source, map_rel)):
                out = relative(pending, dest)
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("xb") as stream:
                    stream.write(source.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
            record, mapping = read(pending / record_rel), read(pending / map_rel)
            require(file_hash(pending / record_rel) == pick["revision_file_sha256"],
                    "Selected record changed during snapshot")
            require(file_hash(pending / map_rel) == pick["frame_map_sha256"],
                    "Selected map changed during snapshot")
            entry = {
                "video_id": sid, "dataset_entry": copy.deepcopy(item),
                "source": {"root_alias": "DATA_ROOT", "locator": item["source_path"],
                           "sha256": file_hash(relative(root, item["source_path"])),
                           "bytes": relative(root, item["source_path"]).stat().st_size},
                "revision_id": rid, "revision_ref": revision_ref,
                "record": _file_info(pending / record_rel, record_rel),
                "canonical_record_sha256": record_hash(record),
                "record_format": record["format"], "label_format": record["label"]["format"],
                "profile": profile_of(record), "frame_map_ref": item["frame_map_ref"],
                "record_frame_map_ref": record_frame_map_ref(item),
                "map_alias": ({"alias": record_frame_map_ref(item), "target": item["frame_map_ref"],
                               "basis": "dataset_entry.imported_revision"}
                              if item.get("imported_revision") else None),
                "frame_map": _file_info(pending / map_rel, map_rel),
                "map_summary": {k: mapping[k] for k in
                                ("video_id", "source_sha256", "frame_count", "width", "height",
                                 "first_source_ms")},
                "annotation": _summary(record, mapping),
                "development_exposure": "DEV_EXPOSED", "groups": copy.deepcopy(pick["groups"]),
            }
            entries.append(entry)
        evidence_source = _checked_file(root, selection["user_evidence"])
        evidence_dest = relative(pending, "evidence/user-review.json")
        evidence_dest.parent.mkdir()
        evidence_dest.write_bytes(evidence_source.read_bytes())
        instructions = (
            "Data-side DEV handoff v0.1; not a pipeline-adopted contract or TEST truth.\n"
            "Bind DATA_ROOT once to the annotation workspace local directory (read only).\n"
            "record/frame_map snapshot paths are relative to this handoff directory.\n"
            "source.locator and frame-map frames[].image_path are DATA_ROOT relative.\n"
            "Original revision_ref/frame_map_ref are provenance, not latest lookups.\n"
            "Only declared map_alias entries resolve opaque imported map references.\n"
            "Preserve integer pts, rational time_base, fractional source_ms, nonzero origin;\n"
            "END is the last included source frame. No FPS or integer-ms recomputation.\n"
            "source_actor_id is not a runtime target_track mapping.\n"
            "Check manifest bytes against the separately supplied SHA256; hashes are not signatures.\n"
            "Use read_dev_handoff or dev-handoff-check (docs/dev-handoff.md).\n"
            "Never write to the data ledger or modify this package; use new revision/handoff IDs.\n"
        ).encode("utf-8")
        (pending / "README.txt").write_bytes(instructions)
        manifest = {
            "format": FORMAT, "version": "0.1", "handoff_id": handoff_id,
            "purpose": "DEV", "created_at_kst": now(), "profile": PROFILE,
            "publisher": _tool_fingerprint(), "source_tools": selection["source_tools"],
            "asset_roots": ASSET_ROOTS, "time_axis": TIME_AXIS, "policy": POLICY,
            "selected_ids": ids, "selected_count": len(ids), "entries": entries,
            "user_evidence": _file_info(evidence_dest, "evidence/user-review.json"),
            "instructions": _file_info(pending / "README.txt", "README.txt"),
        }
        manifest_bytes = encoded(manifest)
        (pending / "manifest.json").write_bytes(manifest_bytes)
        digest = digest_bytes(manifest_bytes)
        (pending / "manifest.sha256").write_bytes((digest + "  manifest.json\n").encode("ascii"))
        read_dev_handoff(pending, media_root=root, expected_manifest_sha256=digest, expected_ids=ids)
        # Same parent/volume, exclusive target and serialized publication. Failed staging
        # stays .pending-* for diagnosis; it is never reported as a published handoff.
        require(not target.exists(), "Handoff appeared during publication")
        os.rename(pending, target)
    return {"status": "PUBLISHED", "path": str(target), "manifest": str(target / "manifest.json"),
            "manifest_sha256": digest, "ids": ids, "format": FORMAT}
