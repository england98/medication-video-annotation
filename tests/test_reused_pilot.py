"""Selected upstream synthetic tests; fixture constructor adapted to the common template."""

import pytest

from medication_video_annotation.legacy_record import validate_record
from medication_video_annotation.records import instantiate


def blank_record(sample):
    return instantiate(
        sample["sample_id"], sample["source_sha256"], "frames/synthetic/frame-map.json"
    )


@pytest.fixture
def sample():
    return {
        "sample_id": "ONC110-T01",
        "video_id": "ONC110-T01",
        "source_sha256": "a" * 64,
        "source_kind": "SYNTHETIC_FORMAT_TEST",
        "width": 1920,
        "height": 1080,
        "frame_count": 5,
        "frames": [
            {"frame_index": i, "source_ms": ms, "player_elapsed_ms": ms - 1400}
            for (i, ms) in enumerate([1400, 1440, 1513, 1560, 1600])
        ],
    }


@pytest.fixture
def record(sample):
    result = blank_record(sample)
    result["analysis"]["status"] = "DRAFT"
    result["analysis"]["observation_log"] = [
        {
            "kind": "AI_VISUAL_OBSERVATION",
            "observer_ref": "synthetic-test",
            "recorded_at_kst": "2026-10-16T11:00:00+09:00",
            "input_set_id": "test-input",
            "start_frame_index": 0,
            "end_frame_index": 4,
            "display_width": 1920,
            "display_height": 1080,
        }
    ]
    label = result["label"]
    label["coverage"] = {
        "annotation_status": "DRAFT",
        "reviewed_ranges": [{"start_frame_index": 0, "end_frame_index": 4}],
        "event_types": ["E01", "E02", "E03"],
        "exhaustive": True,
    }
    label["events"] = [
        {
            "event_id": "drink1",
            "event_type": "E03",
            "annotation_status": "DRAFT",
            "subject_key": "person1",
            "object_key": "cup1",
            "interval": {
                "start_frame_index": 1,
                "end_frame_index": 4,
                "start_ms": 1440,
                "end_ms": 1600,
                "boundary_status": "APPROXIMATE",
                "truncated_start": False,
                "truncated_end": None,
            },
            "context_ranges": [
                {
                    "role": "APPROACH",
                    "start_frame_index": 0,
                    "end_frame_index": 0,
                    "start_ms": 1400,
                    "end_ms": 1400,
                }
            ],
            "check_answers": {
                "container_reaches_mouth": "YES",
                "drinking_motion_visible": "UNKNOWN",
            },
            "evidence": [
                {
                    "check": check,
                    "frame_index": index,
                    "source_ms": sample["frames"][index]["source_ms"],
                    "role": "OBSERVATION",
                    "note": "synthetic evidence",
                }
                for (check, index) in [
                    ("container_reaches_mouth", 1),
                    ("drinking_motion_visible", 2),
                ]
            ],
            "limitations": [
                {"check": "drinking_motion_visible", "reason": "OCCLUDED", "frame_indices": [2]}
            ],
            "links": [],
            "verification_rule_version": "architecture-2-4-current",
            "reference_verification": None,
        }
    ]
    return result


def test_blank_does_not_claim_absence_or_observation(sample):
    result = validate_record(blank_record(sample), sample)
    assert not result["label"]["coverage"]["exhaustive"]
    assert result["analysis"]["observation_log"] == []
    assert not result["label"]["events"]
    assert result["label"]["session_label"]["annotation_status"] == "UNANNOTATED"


@pytest.mark.parametrize(
    "failure",
    [
        "source",
        "pts",
        "unobserved",
        "sparse_exhaustive",
        "unknown_evidence",
        "exact",
        "derived",
        "session",
        "evaluation",
        "category",
        "decode_as_observation",
        "unannotated_coverage",
    ],
)
def test_invalid_records_fail_before_save(sample, record, failure):
    event = record["label"]["events"][0]
    if failure == "source":
        record["label"]["source_sha256"] = "b" * 64
    elif failure == "pts":
        event["interval"]["start_ms"] = 40
    elif failure == "unobserved":
        record["analysis"]["observation_log"][0]["start_frame_index"] = 2
    elif failure == "sparse_exhaustive":
        record["label"]["coverage"]["reviewed_ranges"][0]["end_frame_index"] = 3
    elif failure == "unknown_evidence":
        event["evidence"].pop()
    elif failure == "exact":
        event["interval"]["boundary_status"] = "EXACT"
    elif failure == "derived":
        event["reference_verification"] = "CONFIRMED"
    elif failure == "session":
        record["label"]["session_label"]["expected_result"] = "COMPLETE"
    elif failure == "evaluation":
        record["label"]["evaluation_eligible"] = True
    elif failure == "category":
        del record["analysis"]["categories"]["people"]
    elif failure == "decode_as_observation":
        record["analysis"]["observation_log"][0]["kind"] = "DECODE"
    else:
        record["label"]["coverage"]["annotation_status"] = "UNANNOTATED"
    with pytest.raises(ValueError):
        validate_record(record, sample)


def test_medication_identity_requires_link_and_session_requires_review(sample, record):
    event = record["label"]["events"][0]
    event["event_type"] = "E02"
    event["check_answers"] = {
        "object_is_medication": "YES",
        "object_transfer_into_mouth_visible": "YES",
    }
    event["evidence"][0]["check"] = "object_is_medication"
    event["evidence"][1]["check"] = "object_transfer_into_mouth_visible"
    event["limitations"] = []
    with pytest.raises(ValueError, match="same-object"):
        validate_record(record, sample)
    event["links"] = [
        {
            "relation": "SAME_TRANSFER_OBJECT",
            "status": "CONFIRMED",
            "frame_indices": [1, 2],
            "note": "synthetic linkage test",
        }
    ]
    validate_record(record, sample)
    event["annotation_status"] = "REVIEWED"
    event["reference_verification"] = "CONFIRMED"
    record["label"]["review"].update(
        reviewer_ref="synthetic-reviewer", reviewed_at_kst="2026-10-16T11:00:00+09:00"
    )
    record["label"]["session_label"].update(
        annotation_status="REVIEWED",
        completion_evidence="SUFFICIENT",
        expected_result="COMPLETE",
        supporting_event_ids=[event["event_id"]],
        subject_key=event["subject_key"],
        reviewed_ranges=[{"start_frame_index": 0, "end_frame_index": 4}],
        reason="synthetic session test",
    )
    validate_record(record, sample)
    record["label"]["session_label"]["subject_key"] = "different-person"
    with pytest.raises(ValueError, match="E02 evidence"):
        validate_record(record, sample)
