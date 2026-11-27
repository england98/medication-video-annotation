"""Validation/CSV reused from onc110_pilot_records.py; CVAT transport excluded.
Source and function hashes: docs/reuse-provenance.json.
"""

import csv
import io
import json
import re

from .io import require

ANALYSIS_FIELDS = (
    "source_and_duplicates",
    "people",
    "capture",
    "environment",
    "view_and_visibility",
    "behavior",
    "technical_quality",
    "evaluation_use",
)
STATUSES = {"UNANNOTATED", "DRAFT", "REVIEWED"}
CHECKS = {
    "E01": ("handling_visible", "contents_removed_visible"),
    "E02": ("object_is_medication", "object_transfer_into_mouth_visible"),
    "E03": ("container_reaches_mouth", "drinking_motion_visible"),
}


def interval_indices(item, sample, with_pts=False):
    a, b = item["start_frame_index"], item["end_frame_index"]
    require(
        type(a) is int and type(b) is int and 0 <= a <= b < len(sample["frames"]),
        "Invalid source frame range",
    )
    if with_pts:
        require(
            item["start_ms"] == sample["frames"][a]["source_ms"]
            and item["end_ms"] == sample["frames"][b]["source_ms"],
            "Source PTS mismatch",
        )
    return set(range(a, b + 1))


def observed_indices(record, sample):
    seen = set()
    for entry in record["analysis"]["observation_log"]:
        require(
            entry.get("kind") in {"AI_VISUAL_OBSERVATION", "HUMAN_VISUAL_OBSERVATION"},
            "Decode/display preparation is not visual observation",
        )
        require(
            entry.get("observer_ref")
            and entry.get("recorded_at_kst")
            and entry.get("input_set_id"),
            "Observation provenance missing",
        )
        require(
            type(entry.get("display_width")) is int
            and entry["display_width"] > 0
            and type(entry.get("display_height")) is int
            and entry["display_height"] > 0,
            "Actual display dimensions required",
        )
        seen |= interval_indices(entry, sample)
    return seen


def reference_verification(kind, answers):
    values = [answers[name] for name in CHECKS[kind]]
    if None in values:
        return None
    if kind == "E01":
        return {"YES": "CONFIRMED", "NO": "REFUTED", "UNKNOWN": "UNCERTAIN"}[values[0]]
    return (
        "REFUTED" if "NO" in values else ("CONFIRMED" if values == ["YES", "YES"] else "UNCERTAIN")
    )


def validate_record(record, sample):
    require(
        record.get("format") == "onc110-analysis-label-record-v0.1"
        and record.get("video_id") == sample["sample_id"],
        "Record identity mismatch",
    )
    analysis, label = record["analysis"], record["label"]
    require(
        analysis["status"] in STATUSES and set(analysis["categories"]) == set(ANALYSIS_FIELDS),
        "All eight analysis categories required",
    )
    seen = observed_indices(record, sample)
    for category in analysis["categories"].values():
        require(category["status"] in STATUSES, "Invalid analysis status")
        for observation in category["observations"]:
            require(
                observation.get("basis") in {"METADATA", "DIRECT_OBSERVATION"}
                and observation.get("note"),
                "Analysis basis/note missing",
            )
            if observation["basis"] == "DIRECT_OBSERVATION":
                indices = observation.get("frame_indices", [])
                require(
                    indices and all(type(i) is int and i in seen for i in indices),
                    "Analysis references unobserved frames",
                )
    require(
        label.get("format") == "onc110-video-label-v0.1"
        and label["video_id"] == sample["sample_id"]
        and label["source_sha256"] == sample["source_sha256"],
        "Label/source identity mismatch",
    )
    for field in ("source_ref", "frame_map_ref", "label_version", "guideline_version"):
        require(bool(label.get(field)), "Missing label field: " + field)
    require(
        label.get("exchange_contract") is False
        and label.get("final_ground_truth") is False
        and label.get("evaluation_eligible") is False,
        "Development records cannot become evaluation truth",
    )
    coverage = label["coverage"]
    require(
        coverage["annotation_status"] in STATUSES and type(coverage["exhaustive"]) is bool,
        "Invalid coverage status",
    )
    covered = set()
    for span in coverage["reviewed_ranges"]:
        covered |= interval_indices(span, sample)
    require(
        covered <= seen and set(coverage["event_types"]) <= set(CHECKS),
        "Coverage exceeds observed frames/types",
    )
    if coverage["annotation_status"] == "UNANNOTATED":
        require(
            not covered and not coverage["event_types"] and not coverage["exhaustive"],
            "Unannotated coverage must remain empty",
        )
    if coverage["exhaustive"]:
        require(
            covered == set(range(len(sample["frames"])))
            and set(coverage["event_types"]) == set(CHECKS),
            "Exhaustive coverage requires all frames and event types",
        )
    review = label["review"]
    require(
        review["independent_review"] == "NOT_RUN",
        "Independent evaluation review is a separate workflow",
    )
    ids = set()
    for event in label["events"]:
        eid, kind = event["event_id"], event["event_type"]
        require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", eid)
            and eid not in ids
            and kind in CHECKS,
            "Invalid/duplicate event ID or event type",
        )
        ids.add(eid)
        require(event["annotation_status"] in STATUSES, "Invalid event annotation status")
        for field in (
            "subject_key",
            "object_key",
            "context_ranges",
            "limitations",
            "links",
            "reference_verification",
            "verification_rule_version",
        ):
            require(field in event, "Missing event field: " + field)
        bounds = event["interval"]
        for marker in event.get("cvat_markers", []):
            require(
                type(marker.get("frame_index")) is int and marker["frame_index"] in seen,
                "Unobserved retained CVAT marker",
            )
        if bounds is not None:
            require(interval_indices(bounds, sample, True) <= seen, "Unobserved event interval")
            require(
                bounds["boundary_status"] in {"EXACT", "APPROXIMATE", "UNCONFIRMED"},
                "Invalid boundary status",
            )
            require(
                all(
                    bounds[k] is None or type(bounds[k]) is bool
                    for k in ("truncated_start", "truncated_end")
                ),
                "Invalid truncation flags",
            )
            if bounds["boundary_status"] == "EXACT":
                require(
                    event["annotation_status"] == "REVIEWED"
                    and review.get("reviewer_ref")
                    and review.get("reviewed_at_kst"),
                    "Exact boundary requires human review",
                )
        for context in event["context_ranges"]:
            require(
                context.get("role") and interval_indices(context, sample, True) <= seen,
                "Invalid/unobserved context",
            )
        answers = event["check_answers"]
        require(
            set(answers) == set(CHECKS[kind])
            and all(v in {None, "YES", "NO", "UNKNOWN"} for v in answers.values()),
            "Invalid check answers",
        )
        if event["annotation_status"] == "UNANNOTATED":
            require(all(v is None for v in answers.values()), "Unannotated answers must be null")
        supported = set()
        for evidence in event["evidence"]:
            i = evidence["frame_index"]
            require(
                type(i) is int
                and i in seen
                and evidence["check"] in CHECKS[kind]
                and evidence["source_ms"] == sample["frames"][i]["source_ms"]
                and evidence.get("role")
                and evidence.get("note"),
                "Invalid/unobserved check evidence",
            )
            supported.add(evidence["check"])
        require(
            all(v is None or k in supported for k, v in answers.items()),
            "Every answer including UNKNOWN needs observed evidence",
        )
        for limitation in event["limitations"]:
            require(
                limitation.get("check") in CHECKS[kind]
                and limitation.get("reason")
                and limitation.get("frame_indices")
                and all(type(i) is int and i in seen for i in limitation["frame_indices"]),
                "Invalid limitation evidence",
            )
        for link in event["links"]:
            require(
                link.get("status") in {"CONFIRMED", "UNCONFIRMED"}
                and link.get("relation")
                and link.get("note")
                and link.get("frame_indices")
                and all(type(i) is int and i in seen for i in link["frame_indices"]),
                "Invalid object link",
            )
        require(
            not (
                kind == "E01"
                and answers[CHECKS[kind][0]] == "NO"
                and answers[CHECKS[kind][1]] == "YES"
            ),
            "Contradictory handling/removal answers",
        )
        if kind == "E02" and all(v == "YES" for v in answers.values()):
            require(
                event["subject_key"]
                and event["object_key"]
                and any(
                    link["relation"] == "SAME_TRANSFER_OBJECT" and link["status"] == "CONFIRMED"
                    for link in event["links"]
                ),
                "Medication/transfer requires same-object evidence",
            )
        expected = (
            reference_verification(kind, answers)
            if event["annotation_status"] == "REVIEWED"
            else None
        )
        require(
            event["reference_verification"] == expected,
            "Reference verification must follow reviewed answers",
        )
        if event["annotation_status"] == "REVIEWED":
            require(
                review.get("reviewer_ref")
                and review.get("reviewed_at_kst")
                and None not in answers.values(),
                "Reviewed event needs reviewer and completed answers",
            )
    if coverage["annotation_status"] == "REVIEWED":
        require(
            review.get("reviewer_ref") and review.get("reviewed_at_kst"),
            "Coverage review provenance missing",
        )
    for observation in label["observations"]:
        require(
            observation.get("kind")
            and observation.get("reason")
            and interval_indices(observation, sample, True) <= seen,
            "Invalid absence/limitation observation",
        )
    session = label["session_label"]
    require(
        session["scope"] in {"CLIP_OBSERVATION_WINDOW", "CONTINUOUS_SESSION"}
        and session["annotation_status"] in {"UNANNOTATED", "REVIEWED", "NOT_APPLICABLE"},
        "Invalid session scope/status",
    )
    require(set(session["supporting_event_ids"]) <= ids, "Unknown supporting event")
    if session["annotation_status"] != "REVIEWED":
        require(
            session["completion_evidence"] is None and session["expected_result"] is None,
            "Unreviewed/inapplicable session cannot have an outcome",
        )
        if session["annotation_status"] == "NOT_APPLICABLE":
            require(bool(session["reason"]), "Session applicability reason required")
    else:
        require(
            review.get("reviewer_ref")
            and review.get("reviewed_at_kst")
            and session.get("reason")
            and session.get("subject_key")
            and session.get("reviewed_ranges"),
            "Session review context missing",
        )
        for span in session["reviewed_ranges"]:
            require(interval_indices(span, sample) <= seen, "Unobserved session scope")
        level = session["completion_evidence"]
        require(
            level in {"SUFFICIENT", "NOT_ESTABLISHED", "INDETERMINATE"},
            "Invalid completion evidence",
        )
        require(
            session["expected_result"] == ("COMPLETE" if level == "SUFFICIENT" else None),
            "Session expected result mismatch",
        )
        if level == "SUFFICIENT":
            require(
                not session["unresolved_conflicts"]
                and any(
                    e["event_id"] in session["supporting_event_ids"]
                    and e["event_type"] == "E02"
                    and e["subject_key"] == session["subject_key"]
                    and e["reference_verification"] == "CONFIRMED"
                    for e in label["events"]
                ),
                "Session completion lacks reviewed E02 evidence",
            )
    for measurement in record["measurements"]:
        require(
            measurement["stage"]
            in {"PREPARATION", "ANALYSIS_RECORDING", "HUMAN_REVIEW", "CORRECTION"}
            and isinstance(measurement["seconds"], (int, float))
            and 0 <= measurement["seconds"] < float("inf")
            and measurement.get("actor_ref")
            and measurement.get("recorded_at_kst"),
            "Invalid timing measurement",
        )
    return record


def events_csv(record):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "video_id",
            "event_id",
            "event_type",
            "annotation_status",
            "start_frame_index",
            "end_frame_index",
            "start_ms",
            "end_ms",
            "check_answers",
            "evidence",
            "limitations",
        ],
    )
    writer.writeheader()
    for event in record["label"]["events"]:
        writer.writerow(
            {
                "video_id": record["video_id"],
                **{k: event[k] for k in ("event_id", "event_type", "annotation_status")},
                **{
                    k: (event["interval"] or {}).get(k)
                    for k in ("start_frame_index", "end_frame_index", "start_ms", "end_ms")
                },
                **{
                    k: json.dumps(event[k], ensure_ascii=False)
                    for k in ("check_answers", "evidence", "limitations")
                },
            }
        )
    return stream.getvalue()
