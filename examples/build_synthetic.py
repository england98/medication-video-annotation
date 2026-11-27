"""Regenerate ONE synthetic export example plus linkage metadata; never use real inputs."""

import hashlib
import json
from pathlib import Path

from medication_video_annotation.export_format import bundle_bytes
from medication_video_annotation.io import encoded
from medication_video_annotation.legacy_record import ANALYSIS_FIELDS
from medication_video_annotation.records import instantiate, validate_draft, validate_link

ROOT = Path(__file__).resolve().parents[1]
SOURCE_BYTES = b"SYNTHETIC METADATA FIXTURE ONLY - NOT A REAL VIDEO\n"
AT = "2026-10-17T00:00:00+09:00"


def fixture():
    sid = "SYNTH-0001"
    base = "data/Tier2/합성_유사행동"
    stem = "합성 예제_001"
    source = f"{base}/videos/{stem}.mp4"
    mapping = f"{base}/frames/{stem}/frame-map.json"
    sha = hashlib.sha256(SOURCE_BYTES).hexdigest()
    frames = [
        {
            "frame_index": i,
            "source_ms": 5000 + i * 40,
            "player_elapsed_ms": i * 40,
            "image_path": f"{base}/frames/{stem}/frame_{i:06d}.png",
        }
        for i in range(3)
    ]
    frame_map = {
        "format": "onc110-frame-map-v0.1",
        "video_id": sid,
        "source_sha256": sha,
        "source_path": source,
        "width": 640,
        "height": 360,
        "frame_count": 3,
        "first_source_ms": 5000,
        "frames": frames,
        "generation": {
            "sampling": "EVERY_SOURCE_FRAME",
            "resize": False,
            "decoder_version": "SYNTHETIC-NOT-DECODED",
            "tool_version": "synthetic-example-v0.1",
        },
    }
    item = {
        "format": "onc110-dataset-index-entry-v0.1",
        "video_id": sid,
        "source_sha256": sha,
        "source_path": source,
        "original_filename": stem + ".mp4",
        "classification": {"tier": "Tier2", "behavior": "합성_유사행동"},
        "frame_map_ref": mapping,
        "record_dir": f"records/{sid}",
        "source_lineage": {
            "origin": "SYNTHETIC",
            "original_path": None,
            "same_person_identity": "UNKNOWN",
            "aliases": [],
        },
    }
    record = instantiate(sid, sha, mapping)
    record["analysis"].update(
        status="DRAFT", analyst="synthetic-author", model_identifier="NOT_OBSERVED"
    )
    record["analysis"]["observation_log"] = [
        {
            "kind": "AI_VISUAL_OBSERVATION",
            "observer_ref": "synthetic-observer-not-a-real-observation",
            "recorded_at_kst": AT,
            "input_set_id": "SYNTH-INPUT",
            "start_frame_index": 0,
            "end_frame_index": 2,
            "display_width": 640,
            "display_height": 360,
            "note": "형식 설명용 가상 관찰. 실제 영상 관찰/AI 수행 결과가 아니다.",
        }
    ]
    for key in ANALYSIS_FIELDS:
        record["analysis"]["categories"][key] = {
            "status": "DRAFT",
            "observations": [
                {
                    "basis": "DIRECT_OBSERVATION",
                    "note": f"합성 설명용 {key}; 실제 판단 아님.",
                    "frame_indices": [0, 1, 2],
                }
            ],
        }
    span = {"start_frame_index": 0, "end_frame_index": 2, "start_ms": 5000, "end_ms": 5080}
    record["label"].update(
        label_version="v0.1-draft",
        coverage={
            "annotation_status": "DRAFT",
            "reviewed_ranges": [span],
            "event_types": ["E01", "E02", "E03"],
            "exhaustive": True,
        },
    )
    record["label"]["events"] = [
        {
            "event_id": "SYNTH-E02-1",
            "event_type": "E02",
            "annotation_status": "DRAFT",
            "subject_key": None,
            "object_key": None,
            "interval": {
                **span,
                "boundary_status": "APPROXIMATE",
                "truncated_start": None,
                "truncated_end": None,
            },
            "context_ranges": [],
            "check_answers": {
                "object_is_medication": "UNKNOWN",
                "object_transfer_into_mouth_visible": "YES",
            },
            "evidence": [
                {
                    "check": check,
                    "frame_index": 1,
                    "source_ms": 5040,
                    "role": "LIMITATION" if check == "object_is_medication" else "OBSERVATION",
                    "note": "합성 예제: 가림/전달 근거 설명.",
                }
                for check in ("object_is_medication", "object_transfer_into_mouth_visible")
            ],
            "limitations": [
                {
                    "check": "object_is_medication",
                    "reason": "합성 예제: 물체가 가려짐.",
                    "frame_indices": [1],
                }
            ],
            "links": [],
            "reference_verification": None,
            "verification_rule_version": "architecture-2-4-current",
        }
    ]
    record["label"]["session_label"].update(
        annotation_status="NOT_APPLICABLE", reason="합성 클립이며 실제 세션 맥락 없음."
    )
    record["revision_history"] = [
        {
            "at_kst": AT,
            "actor_ref": "synthetic-author",
            "operation": "FIRST_VALID_DRAFT",
            "previous_sha256": None,
        }
    ]
    return item, frame_map, record


def main():
    item, mapping, record = fixture()
    sample = validate_link(ROOT / "local", item, mapping)
    validate_draft(record, sample, item["frame_map_ref"])
    linkage = ROOT / "examples/linkage"
    linkage.mkdir(parents=True, exist_ok=True)
    (linkage / "frame-map.json").write_bytes(encoded(mapping))
    (linkage / "dataset-index.jsonl").write_text(
        json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    bundle = bundle_bytes(
        record,
        revision_ref=f"{item['record_dir']}/revision-000000.json",
        revision_file_sha256=hashlib.sha256(encoded(record)).hexdigest(),
        exported_at_kst=AT,
        purpose="SYNTHETIC_FORMAT_EXAMPLE_NOT_A_REAL_EXPORT",
    )
    out = ROOT / "templates/exports/synthetic"
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in bundle.items():
        (out / name).write_bytes(payload)
    print("Synthetic five-file example and two linkage metadata files generated; no real media.")


if __name__ == "__main__":
    main()
