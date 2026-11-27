"""Explicit review, checkpoints, fixed-revision reads, and user-requested export."""

import copy
import os
import re
import uuid
from pathlib import Path

from .behavior_profile import (
    SUPPORTED,
    blank_annotation,
    default_authoring,
    profile_of,
    require_legacy,
    validate_behavior,
)
from .export_format import bundle_bytes
from .io import atomic_json, file_hash, identifier, now, read, relative, require, writer_lock
from .legacy_record import reference_verification, validate_record
from .records import (
    content,
    record_frame_map_ref,
    record_hash,
    revision_paths,
    save_revision,
    validate_draft,
    validate_link,
)


def latest(root, item):
    files = revision_paths(root, item)
    if not files:
        return None, None
    record = read(files[-1])
    if item.get("imported_revision"):
        mapping = read(relative(root, item["frame_map_ref"]))
        validate_draft(record, validate_link(root, item, mapping), record_frame_map_ref(item))
    return record, files[-1]


def begin_profile(record, item, *, profile, revision_id, revision_file_sha256, actor):
    """Memory-only start of new-meaning work; v0.1 label is kept verbatim as history."""
    require(profile in SUPPORTED, "Unsupported semantic profile")
    require(profile_of(record) is None, "Record already uses a semantic profile")
    result = copy.deepcopy(record)
    base = None
    if revision_id is not None:
        base = {
            "revision_id": revision_id,
            "record_sha256": record_hash(record),
            "revision_file_sha256": revision_file_sha256,
        }
        # Inherited REVIEWED applied to the old meaning; the new record starts unreviewed.
        result["analysis"]["status"] = "DRAFT"
        for category in result["analysis"]["categories"].values():
            category["status"] = "DRAFT"
    result["label"]["semantic_profile"] = profile
    result["label"]["behavior_annotation"] = blank_annotation(
        authoring=default_authoring(actor), base_revision=base
    )
    return result


def _normalize_profile(old, incoming, actor, *, reviewing):
    result = copy.deepcopy(incoming)
    require(profile_of(result) is not None, "Cannot drop the active semantic profile")
    block = result["label"]["behavior_annotation"]
    blank = blank_annotation(authoring=None)["review"]
    old_block = old["label"].get("behavior_annotation") if old else None
    if old:
        for key in ("format", "video_id", "revision_history", "measurements"):
            require(result[key] == old[key], "Protected record field changed: " + key)
    if old_block is None:
        require(
            block["review"] == blank
            and result["analysis"]["status"] == "DRAFT"
            and all(c["status"] == "DRAFT" for c in result["analysis"]["categories"].values())
            and all(e["interval"]["boundary_status"] != "EXACT" for e in block["events"]),
            "First new-profile save is an unreviewed draft",
        )
        return result
    for key in (
        "format", "video_id", "source_ref", "source_sha256", "frame_map_ref",
        "final_ground_truth", "evaluation_eligible", "exchange_contract", "semantic_profile",
    ):
        require(result["label"][key] == old["label"][key], "Protected label field changed: " + key)
    for key in ("profile", "guideline_ref", "basis", "review", "final_ground_truth",
                "evaluation_eligible"):
        require(block[key] == old_block[key], "Protected profile field changed: " + key)
    require(result["analysis"]["status"] == old["analysis"]["status"], "Use explicit review action")
    for key, category in result["analysis"]["categories"].items():
        require(
            category["status"] == old["analysis"]["categories"][key]["status"],
            "Use explicit category review action",
        )
    before = {e["event_id"]: e for e in old_block["events"]}
    for event in block["events"]:
        prior = before.get(event["event_id"])
        if not reviewing and event["interval"]["boundary_status"] == "EXACT":
            require(
                prior and prior["interval"]["boundary_status"] == "EXACT",
                "EXACT requires explicit boundary review",
            )
    if content(old) == content(result):
        return result
    human = block["review"]["human_review"]
    if human["status"] == "REVIEWED" and not reviewing:
        # Conservative invalidation of the new-profile review only; v0.1 history untouched.
        result["analysis"]["status"] = "DRAFT"
        for category in result["analysis"]["categories"].values():
            category["status"] = "DRAFT"
        for event in block["events"]:
            if event["interval"]["boundary_status"] == "EXACT":
                event["interval"]["boundary_status"] = "APPROXIMATE"
        human.update(status="NOT_RUN", reviewer_ref=None, reviewed_at_kst=None, reviewed_ranges=[])
        human["history"].append(
            {
                "at_kst": now(),
                "actor_ref": actor,
                "operation": "RE_REVIEW_REQUIRED",
                "scope": "WHOLE_CLIP",
                "previous_record_sha256": record_hash(old),
                "reason": "Content changed after review",
            }
        )
    return result


def normalize_edit(old, incoming, actor, *, reviewing=False):
    if profile_of(incoming) is not None or (old is not None and profile_of(old) is not None):
        return _normalize_profile(old, incoming, actor, reviewing=reviewing)
    result = copy.deepcopy(incoming)
    if old is None:
        require(
            result["label"]["review"]["reviewer_ref"] is None
            and result["label"]["review"]["reviewed_at_kst"] is None
            and result["analysis"]["status"] == "DRAFT"
            and result["label"]["coverage"]["annotation_status"] == "DRAFT"
            and all(c["status"] == "DRAFT" for c in result["analysis"]["categories"].values())
            and all(
                e["annotation_status"] == "DRAFT" and e["reference_verification"] is None
                for e in result["label"]["events"]
            )
            and result["label"]["session_label"]["annotation_status"] != "REVIEWED",
            "First save is an unreviewed draft",
        )
        return result
    for key in ("format", "video_id", "revision_history", "measurements"):
        require(result[key] == old[key], "Protected record field changed: " + key)
    for key in (
        "format",
        "video_id",
        "source_ref",
        "source_sha256",
        "frame_map_ref",
        "review",
        "final_ground_truth",
        "evaluation_eligible",
        "exchange_contract",
    ):
        require(result["label"][key] == old["label"][key], "Protected label field changed: " + key)
    require(result["analysis"]["status"] == old["analysis"]["status"], "Use explicit review action")
    for key, category in result["analysis"]["categories"].items():
        require(
            category["status"] == old["analysis"]["categories"][key]["status"],
            "Use explicit category review action",
        )
    require(
        result["label"]["session_label"] == old["label"]["session_label"],
        "Session verdict requires a separate supported review workflow",
    )
    require(
        result["label"]["coverage"]["annotation_status"]
        == old["label"]["coverage"]["annotation_status"],
        "Use explicit review action",
    )
    old_events = {e["event_id"]: e for e in old["label"]["events"]}
    for event in result["label"]["events"]:
        before = old_events.get(event["event_id"])
        require(
            event["annotation_status"] == (before["annotation_status"] if before else "DRAFT"),
            "Client cannot promote event review",
        )
        require(
            event["reference_verification"]
            == (before["reference_verification"] if before else None),
            "Client cannot edit derived verification",
        )
        if not reviewing and event["interval"] and event["interval"]["boundary_status"] == "EXACT":
            require(
                before and before["interval"] and before["interval"]["boundary_status"] == "EXACT",
                "EXACT requires explicit boundary review",
            )
    if content(old) == content(result):
        return result
    # Conservative invalidation: the complete clip is re-reviewed, never silently promoted.
    was_reviewed = (
        old["analysis"]["status"] == "REVIEWED"
        or old["label"]["coverage"]["annotation_status"] == "REVIEWED"
        or any(e["annotation_status"] == "REVIEWED" for e in old["label"]["events"])
    )
    if was_reviewed:
        session = result["label"]["session_label"]
        if session["annotation_status"] == "REVIEWED":
            session.update(
                annotation_status="UNANNOTATED", completion_evidence=None, expected_result=None
            )
        if reviewing:
            # The same save explicitly re-reviews the edited clip. Keep chosen boundaries.
            return result
        result["analysis"]["status"] = "DRAFT"
        for category in result["analysis"]["categories"].values():
            category["status"] = "DRAFT"
        result["label"]["coverage"]["annotation_status"] = "DRAFT"
        for event in result["label"]["events"]:
            event["annotation_status"], event["reference_verification"] = "DRAFT", None
            if event["interval"] and event["interval"]["boundary_status"] == "EXACT":
                event["interval"]["boundary_status"] = "APPROXIMATE"
        result["label"]["review"].update(reviewer_ref=None, reviewed_at_kst=None)
        result["label"]["review"]["history"].append(
            {
                "at_kst": now(),
                "actor_ref": actor,
                "operation": "RE_REVIEW_REQUIRED",
                "scope": "WHOLE_CLIP",
                "previous_record_sha256": record_hash(old),
                "reason": "Content changed after review",
            }
        )
    return result


def save_with_review(
    root, item, mapping, incoming, *, base_hash, actor, review_status, reviewer_ref, reviewed_ranges
):
    """Validate edits and explicit review selection before one immutable revision write."""
    old, _ = latest(root, item)
    require(base_hash == (record_hash(old) if old else None), "Record changed; reload")
    require(review_status in {"DRAFT", "REVIEWED"}, "Invalid review status")
    require(isinstance(reviewer_ref, str), "Invalid reviewer")
    reviewer = reviewer_ref.strip() or None
    reviewing = review_status == "REVIEWED"
    if reviewing:
        require(reviewer, "Reviewer required")
        expected = [{"start_frame_index": 0, "end_frame_index": mapping["frame_count"] - 1}]
        require(reviewed_ranges == expected, "Explicit full-clip review range required")
    result = normalize_edit(old, incoming, actor, reviewing=reviewing)
    if profile_of(result) is not None:
        return _save_profile_review(
            root, item, mapping, old, result, base_hash=base_hash, actor=actor,
            reviewing=reviewing, reviewer=reviewer, reviewed_ranges=reviewed_ranges,
        )
    result["analysis"]["status"] = review_status
    for category in result["analysis"]["categories"].values():
        category["status"] = review_status
    result["label"]["coverage"]["annotation_status"] = review_status
    for event in result["label"]["events"]:
        event["annotation_status"] = review_status
        event["reference_verification"] = (
            reference_verification(event["event_type"], event["check_answers"])
            if reviewing else None
        )
        if not reviewing and event["interval"] and event["interval"]["boundary_status"] == "EXACT":
            event["interval"]["boundary_status"] = "APPROXIMATE"
    review = result["label"]["review"]
    review["reviewer_ref"] = reviewer
    if not reviewing:
        review["reviewed_at_kst"] = None
        session = result["label"]["session_label"]
        if session["annotation_status"] == "REVIEWED":
            session.update(
                annotation_status="UNANNOTATED", completion_evidence=None, expected_result=None
            )
    # Compare before generating a new timestamp/history, so repeated saves stay no-ops.
    if old is None or content(old) != content(result):
        at = now()
        review["reviewed_at_kst"] = at if reviewing else None
        review["history"].append(
            {
                "at_kst": at,
                "actor_ref": actor,
                "reviewer_ref": reviewer,
                "operation": "HUMAN_REVIEW_COMPLETE" if reviewing else "REVIEW_SELECTION_SAVED",
                "annotation_status": review_status,
                "reviewed_ranges": reviewed_ranges if reviewing else [],
                "exact_event_ids": [
                    e["event_id"] for e in result["label"]["events"]
                    if e["interval"] and e["interval"]["boundary_status"] == "EXACT"
                ],
                "previous_record_sha256": base_hash,
            }
        )
    return save_revision(root, item, mapping, result, base_hash=base_hash, actor=actor)


def _save_profile_review(
    root, item, mapping, old, result, *, base_hash, actor, reviewing, reviewer, reviewed_ranges
):
    """Human confirmation of the new meaning only; never copies v0.1 REVIEWED."""
    status = "REVIEWED" if reviewing else "DRAFT"
    result["analysis"]["status"] = status
    for category in result["analysis"]["categories"].values():
        category["status"] = status
    block = result["label"]["behavior_annotation"]
    if not reviewing:
        for event in block["events"]:
            if event["interval"]["boundary_status"] == "EXACT":
                event["interval"]["boundary_status"] = "APPROXIMATE"
        for actor_info in block["actors"]:
            for link in actor_info.get("runtime_track_links", []):
                link["status"] = "UNCONFIRMED"
    human = block["review"]["human_review"]
    human.update(
        status="REVIEWED" if reviewing else "NOT_RUN",
        reviewer_ref=reviewer if reviewing else None,
        reviewed_ranges=reviewed_ranges if reviewing else [],
    )
    if not reviewing:
        human["reviewed_at_kst"] = None
    if old is None or content(old) != content(result):
        at = now()
        human["reviewed_at_kst"] = at if reviewing else None
        human["history"].append(
            {
                "at_kst": at,
                "actor_ref": actor,
                "reviewer_ref": reviewer,
                "operation": "HUMAN_REVIEW_COMPLETE" if reviewing else "REVIEW_SELECTION_SAVED",
                "annotation_status": status,
                "reviewed_ranges": reviewed_ranges if reviewing else [],
                "exact_event_ids": [
                    e["event_id"] for e in block["events"]
                    if e["interval"]["boundary_status"] == "EXACT"
                ],
                "previous_record_sha256": base_hash,
            }
        )
    return save_revision(root, item, mapping, result, base_hash=base_hash, actor=actor)


def complete_review(root, item, mapping, *, base_hash, actor, reviewed_ranges, exact_event_ids=()):
    old, path = latest(root, item)
    require(path and record_hash(old) == base_hash, "Record changed; reload")
    require(profile_of(old) is None, "Semantic profile review uses the unified save")
    require(actor.strip(), "Reviewer required")
    expected = [{"start_frame_index": 0, "end_frame_index": mapping["frame_count"] - 1}]
    require(reviewed_ranges == expected, "Explicit full-clip review range required")
    require(
        set(exact_event_ids) <= {e["event_id"] for e in old["label"]["events"]},
        "Unknown exact boundary target",
    )
    result = copy.deepcopy(old)
    if (
        old["analysis"]["status"] == "REVIEWED"
        and old["label"]["coverage"]["annotation_status"] == "REVIEWED"
        and all(
            e["interval"] and e["interval"]["boundary_status"] == "EXACT"
            for e in old["label"]["events"]
            if e["event_id"] in exact_event_ids
        )
    ):
        return {"status": "UNCHANGED", "record": old, "base_hash": base_hash, "path": str(path)}
    result["analysis"]["status"] = "REVIEWED"
    for category in result["analysis"]["categories"].values():
        category["status"] = "REVIEWED"
    result["label"]["coverage"]["annotation_status"] = "REVIEWED"
    for event in result["label"]["events"]:
        event["annotation_status"] = "REVIEWED"
        event["reference_verification"] = reference_verification(
            event["event_type"], event["check_answers"]
        )
        if event["event_id"] in exact_event_ids:
            require(event["interval"] is not None, "Cannot confirm a missing boundary")
            event["interval"]["boundary_status"] = "EXACT"
    at = now()
    result["label"]["review"].update(reviewer_ref=actor, reviewed_at_kst=at)
    result["label"]["review"]["history"].append(
        {
            "at_kst": at,
            "actor_ref": actor,
            "operation": "HUMAN_REVIEW_COMPLETE",
            "reviewed_ranges": reviewed_ranges,
            "exact_event_ids": list(exact_event_ids),
            "previous_record_sha256": base_hash,
        }
    )
    # Session outcomes and final/evaluation flags are not automatically promoted.
    return save_revision(root, item, mapping, result, base_hash=base_hash, actor=actor)


def save_checkpoint(
    root, item, record, *, base_hash, checkpoint_hash, actor, cursor, note,
    mapping=None, replace_reason=None,
):
    old, old_path = latest(root, item)
    require(base_hash == (record_hash(old) if old else None), "Revision changed; reload checkpoint")
    require(
        record["video_id"] == item["video_id"]
        and record["label"]["source_sha256"] == item["source_sha256"],
        "Checkpoint identity mismatch",
    )
    require(
        type(cursor) is int and cursor >= 0 and actor.strip(), "Checkpoint cursor/actor required"
    )
    profile = profile_of(record)
    binding = {}
    if profile is not None:
        require(mapping is not None, "Frame map required for a semantic profile checkpoint")
        seen = validate_behavior(
            record, {**mapping, "sample_id": item["video_id"]}, complete=False
        )
        base = record["label"]["behavior_annotation"]["basis"]["base_revision"]
        if old is None or profile_of(old) is None:
            require(
                (base or {}).get("revision_id") == (old_path.name if old_path else None),
                "Checkpoint profile basis differs from the latest revision",
            )
        binding = {
            "semantic_profile": profile,
            "base_revision_id": old_path.name if old_path else None,
            "observation_progress": {
                "observed_frames": len(seen),
                "frame_count": mapping["frame_count"],
                "cursor": cursor,
            },
        }
    directory = Path(root) / "checkpoints"
    with writer_lock(directory):
        path = relative(root, f"checkpoints/{identifier(item['video_id'])}.json")
        previous = read(path) if path.exists() else None
        require(
            checkpoint_hash == (record_hash(previous) if previous else None),
            "Checkpoint changed; reload",
        )
        replaced = None
        if previous and previous.get("semantic_profile") != binding.get("semantic_profile"):
            # Never mix meanings silently in the single latest checkpoint.
            require(
                isinstance(replace_reason, str) and replace_reason.strip(),
                "Existing checkpoint uses another semantic profile; give a replace reason",
            )
            replaced = {
                "checkpoint_sha256": checkpoint_hash,
                "semantic_profile": previous.get("semantic_profile"),
                "reason": replace_reason,
            }
        value = {
            "format": "onc110-checkpoint-v0.2" if profile else "onc110-checkpoint-v0.1",
            "video_id": item["video_id"],
            "source_sha256": item["source_sha256"],
            "base_hash": base_hash,
            **binding,
            "record": record,
            "cursor": cursor,
            "note": note,
            "actor_ref": actor,
            "saved_at_kst": now(),
        }
        if replaced:
            value["replaced_checkpoint"] = replaced
        atomic_json(path, value)
        return {"checkpoint_hash": record_hash(value), "checkpoint": value}


def export_selected(root, item, mapping, *, revision_id, expected_hash, request):
    require(request == "USER_REVIEW_BUTTON", "Explicit review-screen export request required")
    require(re.fullmatch(r"revision-\d{6}\.json", revision_id), "Invalid revision selection")
    source = relative(root, f"{item['record_dir']}/{revision_id}")
    record = read(source)
    require(record_hash(record) == expected_hash, "Selected revision hash mismatch")
    require_legacy(record, "Five-file export v0.1")
    validate_draft(
        record, validate_link(root, item, mapping, verify_files=True), record_frame_map_ref(item)
    )
    source_hash = file_hash(source)
    directory = relative(root, f"exports/{item['video_id']}")
    target = directory / f"{source.stem}_{source_hash[:16]}"
    with writer_lock(directory):
        if target.exists():
            manifest = read(target / "export-manifest.json")
            require(manifest["revision_file_sha256"] == source_hash, "Existing export differs")
            require(
                set(p.name for p in target.iterdir())
                == set(manifest["files"]) | {"export-manifest.json"},
                "Export file set changed",
            )
            for name, info in manifest["files"].items():
                require(
                    file_hash(target / name) == info["sha256"]
                    and (target / name).stat().st_size == info["bytes"],
                    "Existing export damaged",
                )
            return {"status": "REUSED", "path": str(target)}
        files = bundle_bytes(
            record,
            revision_ref=source.relative_to(Path(root).resolve()).as_posix(),
            revision_file_sha256=source_hash,
            exported_at_kst=now(),
            purpose="USER_REVIEW_SCREEN_EXPORT",
        )
        pending = directory / (".pending-" + uuid.uuid4().hex)
        pending.mkdir()
        for name, payload in files.items():
            with (pending / name).open("xb") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
        os.rename(pending, target)
    return {"status": "EXPORTED", "path": str(target)}


def read_release(root, release):
    require(release["format"] == "onc110-release-v0.1", "Invalid release format")
    records = []
    for entry in release["entries"]:
        for field, hash_field in (
            ("source_path", "source_sha256"),
            ("frame_map_ref", "frame_map_sha256"),
            ("revision_ref", "revision_file_sha256"),
        ):
            require(
                file_hash(relative(root, entry[field])) == entry[hash_field],
                "Release file hash mismatch",
            )
        mapping = read(relative(root, entry["frame_map_ref"]))
        record = read(relative(root, entry["revision_ref"]))
        require_legacy(record, "Release reader v0.1")
        require(
            record["video_id"] == mapping["video_id"] == entry["video_id"]
            and record["label"]["source_sha256"]
            == mapping["source_sha256"]
            == entry["source_sha256"]
            and mapping["source_path"] == entry["source_path"]
            and record["label"]["frame_map_ref"] == entry["frame_map_ref"],
            "Release identity mismatch",
        )
        validate_record(record, {**mapping, "sample_id": entry["video_id"]})
        records.append(record)
    return records
