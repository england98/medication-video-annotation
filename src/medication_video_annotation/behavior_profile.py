"""Versioned behavior semantics in label.behavior_annotation; v0.1 fields stay history.

The v0.1 label (events/check_answers/session_label/review) keeps its original meaning.
A record opts in with label.semantic_profile; nothing is converted from old answers.
"""

import re
from fractions import Fraction

from .io import identifier, require, timestamp
from .legacy_record import observed_indices


def log_indices(entries, sample):
    # Same visual-observation rules as analysis.observation_log (reused v0.1 check).
    return observed_indices({"analysis": {"observation_log": entries}}, sample)

PROFILE = "onc110-behavior-annotation-v0.2"
SUPPORTED = {PROFILE}
EXTENSION_KEYS = {"semantic_profile", "behavior_annotation"}
# Fingerprint of the labeling reference recorded in the readiness reference-lock.
GUIDELINE_REF = {
    "name": "labeling-reference.txt",
    "sha256": "9bde48a7fa03ba7893a3fbe2a328729164870a4e39c3e05303337ae98a69b707",
}
QUESTIONS = {
    "E01": ("handling_visible", "contents_removed_visible"),
    "E02": ("hand_to_mouth_visible", "intake_motion_visible"),
    "E03": ("container_reaches_mouth", "drinking_motion_visible"),
}
ANSWERS = {"YES", "NO", "UNKNOWN"}
COVERAGE_STATES = {"PRESENT", "ABSENT", "NOT_COVERED", "UNKNOWN", "NOT_EVALUATED"}
SCOPES = {"CLIP", "FULL_SESSION"}
SCOPE_STATUSES = {"CONFIRMED", "NOT_OBSERVED", "UNCERTAIN", "NOT_EVALUATED"}
ALTERNATIVE_STATUSES = {"NONE", "OBSERVED", "UNRESOLVED"}
ALTERNATIVE_TYPES = {
    "DRINKING", "EATING", "FACE_TOUCH", "COUGH_OR_SNEEZE", "APPROACH_ABORTED", "OTHER"
}
RELATIONS = {"SAME_ACTION", "INDEPENDENT_FOLLOWUP", "UNRESOLVED"}
MOMENT_ROLES = {
    "START", "APPROACH", "MOUTH_CONTACT", "INTAKE_MOTION", "CONTAINER_CONTACT",
    "WITHDRAWAL", "END_LAST_INCLUDED", "OTHER",
}
MODEL_CONFIRMATIONS = {"CONFIRMED_BY_RUNTIME", "UNCONFIRMED", "NOT_APPLICABLE"}
RAW_OUTPUT = {"CAPTURED", "NOT_CAPTURED", "NOT_APPLICABLE"}
SHA256 = re.compile(r"[0-9a-f]{64}")


def profile_of(record):
    """None means the legacy v0.1 meaning. Unknown or half-declared profiles are refused."""
    label = record["label"]
    if not EXTENSION_KEYS & set(label):
        return None
    declared = label.get("semantic_profile")
    require(declared in SUPPORTED, f"Unsupported semantic profile: {declared!r}")
    block = label.get("behavior_annotation")
    require(
        isinstance(block, dict) and block.get("profile") == declared,
        "Semantic profile and behavior_annotation extension disagree",
    )
    return declared


def require_legacy(record, consumer):
    profile = profile_of(record)
    require(
        profile is None,
        f"{consumer} supports only the v0.1 label meaning; {profile} is not supported yet."
        " Nothing was written; profile-aware export/release is a follow-up.",
    )


def legacy_label(record):
    return {k: v for k, v in record["label"].items() if k not in EXTENSION_KEYS}


def blank_annotation(*, authoring, base_revision=None, change_reason=None):
    """Memory-only skeleton; invalid for saving until actually observed and written."""
    return {
        "profile": PROFILE,
        "guideline_ref": dict(GUIDELINE_REF),
        "basis": {"base_revision": base_revision, "change_reason": change_reason},
        "authoring": authoring,
        "observation_log": [],
        "actors": [],
        "events": [],
        "coverage": {
            kind: {"state": "NOT_EVALUATED", "reason": None, "evidence": []} for kind in QUESTIONS
        },
        "relations": [],
        "scope_assessments": [],
        "review": {
            "ai_review": {
                "status": "NOT_RUN",
                "reviewer_ref": None,
                "model_identifier": None,
                "reviewed_at_kst": None,
                "findings": None,
            },
            "human_review": {
                "status": "NOT_RUN",
                "reviewer_ref": None,
                "reviewed_at_kst": None,
                "reviewed_ranges": [],
                "history": [],
            },
            "independent_review": "NOT_RUN",
            "release_freeze": "NOT_RUN",
        },
        "final_ground_truth": False,
        "evaluation_eligible": False,
    }


def default_authoring(actor):
    """Unknowns stay explicit; a model ID or raw output is never filled in by guess."""
    return {
        "author_kind": "AI_MODEL",
        "author_ref": actor,
        "model_identifier": {"value": None, "confirmation": "UNCONFIRMED"},
        "prompt": {"prompt_id": None, "sha256": None},
        "input_set_ids": [],
        "raw_output": {"status": "NOT_CAPTURED", "ref": None, "sha256": None, "reason": None},
    }


def human_reviewed(record):
    if profile_of(record) is None:
        return record["label"]["coverage"]["annotation_status"] == "REVIEWED"
    return record["label"]["behavior_annotation"]["review"]["human_review"]["status"] == "REVIEWED"


def text(value):
    return isinstance(value, str) and bool(value.strip())


def hex_or_none(value):
    return value is None or (isinstance(value, str) and SHA256.fullmatch(value))


def check_model(value, kind, what):
    require(
        isinstance(value, dict) and value.get("confirmation") in MODEL_CONFIRMATIONS,
        what + ": model identifier confirmation required",
    )
    confirmed = value["confirmation"] == "CONFIRMED_BY_RUNTIME"
    require(
        (text(value.get("value")) if confirmed else value.get("value") is None),
        what + ": only a runtime-confirmed model identifier may be recorded",
    )
    require(
        (value["confirmation"] == "NOT_APPLICABLE") == (kind == "HUMAN"),
        what + ": model identifier applicability mismatch",
    )


class Frames:
    """Original frame-map times are authoritative: exact pts/time_base/source_ms only."""

    def __init__(self, sample, seen):
        self.frames, self.seen = sample["frames"], seen
        for frame in self.frames:
            pts, base = frame.get("pts"), frame.get("time_base")
            require(
                type(pts) is int
                and isinstance(base, str)
                and re.fullmatch(r"[1-9]\d*/[1-9]\d*", base)
                and abs(float(Fraction(pts) * Fraction(base) * 1000) - frame["source_ms"]) < 1e-6,
                "Semantic profile requires rational pts/time_base consistent with the frame map",
            )

    def ref(self, ref, what):
        require(
            isinstance(ref, dict) and {"frame_index", "pts", "time_base", "source_ms"} <= set(ref),
            what + ": FrameRef needs frame_index/pts/time_base/source_ms",
        )
        i = ref["frame_index"]
        require(type(i) is int and 0 <= i < len(self.frames), what + ": frame outside video")
        require(i in self.seen, what + ": references an unobserved frame")
        frame = self.frames[i]
        require(
            type(ref["pts"]) is int
            and ref["pts"] == frame["pts"]
            and ref["time_base"] == frame["time_base"]
            and type(ref["source_ms"]) in (int, float)
            and ref["source_ms"] == frame["source_ms"],
            what + ": FrameRef pts/time_base/source_ms differ from the original frame",
        )
        return i

    def refs(self, values, what, *, required=True):
        require(isinstance(values, list), what + ": evidence list required")
        require(values or not required, what + ": observed evidence required")
        return [self.ref(v, what) for v in values]

    def span(self, value, what):
        require(isinstance(value, dict), what + ": range required")
        a = self.ref(value.get("start"), what + " start")
        b = self.ref(value.get("end_last_included"), what + " end")
        require(a <= b, what + ": start after last included frame")
        require(set(range(a, b + 1)) <= self.seen, what + ": range has unobserved frames")
        return a, b


def no_runtime_ids(value, what):
    require(
        not {"runtime_track_id", "target_track_id"} & set(value),
        what + ": runtime track IDs are linked only through evidenced runtime_track_links",
    )


def validate_behavior(record, sample, *, complete):
    """complete=True for saved revisions; False permits checkpoint work in progress."""
    label = record["label"]
    require(profile_of(record) == PROFILE, "Semantic profile record required")
    block = label["behavior_annotation"]
    for key in (
        "guideline_ref", "basis", "authoring", "observation_log", "actors", "events",
        "coverage", "relations", "scope_assessments", "review",
    ):
        require(key in block, "Missing behavior_annotation field: " + key)
    require(
        block.get("final_ground_truth") is False and block.get("evaluation_eligible") is False,
        "Development records cannot become evaluation truth",
    )
    guide = block["guideline_ref"]
    require(
        isinstance(guide, dict)
        and text(guide.get("name"))
        and SHA256.fullmatch(guide.get("sha256") or ""),
        "Guideline name and SHA256 required",
    )
    basis = block["basis"]
    base = basis.get("base_revision")
    if base is not None:
        require(
            re.fullmatch(r"revision-\d{6}\.json", base.get("revision_id") or "")
            and SHA256.fullmatch(base.get("record_sha256") or "")
            and SHA256.fullmatch(base.get("revision_file_sha256") or ""),
            "Base revision needs revision_id/record_sha256/revision_file_sha256",
        )
        require(not complete or text(basis.get("change_reason")), "Re-annotation reason required")

    count = len(sample["frames"])
    seen = log_indices(block["observation_log"], sample)
    frames = Frames(sample, seen)
    full = seen == set(range(count))
    require(not complete or full, "New-profile draft requires its own full observation log")

    authoring = block["authoring"]
    kind = authoring.get("author_kind")
    require(kind in {"AI_MODEL", "HUMAN"} and text(authoring.get("author_ref")), "Author required")
    check_model(authoring.get("model_identifier"), kind, "Authoring")
    prompt = authoring.get("prompt")
    require(
        isinstance(prompt, dict) and hex_or_none(prompt.get("sha256")),
        "Prompt reference must be null or carry a SHA256",
    )
    if complete and kind == "AI_MODEL":
        require(
            text(prompt.get("prompt_id")) and prompt.get("sha256"),
            "AI draft needs prompt ID and SHA256",
        )
    logged = {e["input_set_id"] for e in block["observation_log"]}
    inputs = authoring.get("input_set_ids")
    require(
        isinstance(inputs, list)
        and all(text(v) for v in inputs)
        and len(set(inputs)) == len(inputs),
        "Input set IDs must be unique strings",
    )
    require(
        set(inputs) == logged if complete else logged <= set(inputs),
        "Authoring input sets must match the observation log",
    )
    raw = authoring.get("raw_output")
    require(isinstance(raw, dict) and raw.get("status") in RAW_OUTPUT, "Raw output status required")
    if raw["status"] == "CAPTURED":
        require(
            text(raw.get("ref")) and SHA256.fullmatch(raw.get("sha256") or ""),
            "Captured raw output needs ref and SHA256",
        )
    else:
        require(raw.get("ref") is None and raw.get("sha256") is None, "Uncaptured output has no ref")
        require(
            (raw["status"] == "NOT_APPLICABLE") == (kind == "HUMAN"),
            "Raw output applicability mismatch",
        )
        if complete and raw["status"] == "NOT_CAPTURED":
            require(text(raw.get("reason")), "State why raw output was not captured")

    review = block["review"]
    human, ai = review.get("human_review"), review.get("ai_review")
    require(
        review.get("independent_review") == "NOT_RUN" and review.get("release_freeze") == "NOT_RUN",
        "Independent review/release freeze are separate workflows",
    )
    require(isinstance(human, dict) and human.get("status") in {"NOT_RUN", "REVIEWED"}, "Human review")
    require(isinstance(human.get("history"), list), "Human review history required")
    reviewed = human["status"] == "REVIEWED"
    if reviewed:
        require(
            text(human.get("reviewer_ref"))
            and human.get("reviewed_at_kst")
            and timestamp(human["reviewed_at_kst"])
            and human.get("reviewed_ranges")
            == [{"start_frame_index": 0, "end_frame_index": count - 1}],
            "Human review needs reviewer, server time and full-clip range",
        )
    else:
        require(
            human.get("reviewer_ref") is None
            and human.get("reviewed_at_kst") is None
            and human.get("reviewed_ranges") == [],
            "Unreviewed profile cannot carry review provenance",
        )
    require(isinstance(ai, dict) and ai.get("status") in {"NOT_RUN", "COMPLETE"}, "AI review state")
    if ai["status"] == "COMPLETE":
        require(text(ai.get("reviewer_ref")) and text(ai.get("findings")), "AI review provenance")
        timestamp(ai.get("reviewed_at_kst") or "")
        check_model(ai.get("model_identifier"), "AI_MODEL", "AI review")
    else:
        require(
            all(ai.get(k) is None for k in ("reviewer_ref", "model_identifier", "reviewed_at_kst")),
            "AI review not run",
        )
    if complete:
        expected = "REVIEWED" if reviewed else "DRAFT"
        require(
            record["analysis"]["status"] == expected
            and all(c["status"] == expected for c in record["analysis"]["categories"].values()),
            "Analysis status must follow the new-profile human review; no inherited REVIEWED",
        )

    actors = {}
    for actor in block["actors"]:
        no_runtime_ids(actor, "Actor")
        sid = identifier(actor.get("source_actor_id"))
        require(sid not in actors and text(actor.get("description")), "Unique described actor")
        for link in actor.get("runtime_track_links", []):
            require(
                text(link.get("stream_id"))
                and text(link.get("target_track_id"))
                and link.get("status") in {"UNCONFIRMED", "CONFIRMED"}
                and text(link.get("reason")),
                "Runtime track link needs stream/track/status/reason",
            )
            frames.refs(link.get("evidence"), "Runtime track link")
            require(
                link["status"] == "UNCONFIRMED" or reviewed,
                "Actor-track association is confirmed only by human review",
            )
        actors[sid] = actor
    require(not complete or actors, "At least one source actor required")

    events, spans = {}, {}
    for event in block["events"]:
        no_runtime_ids(event, "Event")
        eid, kind = event.get("event_id"), event.get("event_type")
        require(
            isinstance(eid, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", eid)
            and eid not in events
            and kind in QUESTIONS,
            "Invalid/duplicate event ID or event type",
        )
        require(event.get("source_actor_id") in actors, "Event actor is not a declared source actor")
        identifier(event.get("same_action_group_id"))
        require(event.get("object_id") is None or text(event["object_id"]), "Invalid object ID")
        interval = event.get("interval")
        spans[eid] = frames.span(interval, "Event interval")
        require(
            interval.get("boundary_status") in {"APPROXIMATE", "UNCONFIRMED", "EXACT"},
            "Invalid boundary status",
        )
        require(interval["boundary_status"] != "EXACT" or reviewed, "EXACT needs human review")
        require(
            all(interval.get(k) in (None, True, False) for k in ("truncated_start", "truncated_end")),
            "Invalid truncation flags",
        )
        a, b = spans[eid]
        require(isinstance(event.get("key_moments"), list), "Key moments list required")
        for moment in event["key_moments"]:
            require(moment.get("role") in MOMENT_ROLES, "Invalid key moment role")
            i = frames.ref(moment.get("frame"), "Key moment")
            require(a <= i <= b, "Key moment outside event interval")
            require(moment["role"] != "START" or i == a, "START moment must equal interval start")
            require(
                moment["role"] != "END_LAST_INCLUDED" or i == b,
                "END_LAST_INCLUDED moment must equal interval end",
            )
        questions = event.get("questions")
        require(
            isinstance(questions, dict) and set(questions) == set(QUESTIONS[kind]),
            f"{kind} needs exactly the {PROFILE} questions; v0.1 questions are not reused",
        )
        answers = {}
        for name, value in questions.items():
            answer = value.get("answer")
            require(answer in ANSWERS or (answer is None and not complete), "Invalid/missing answer")
            if answer is not None:
                frames.refs(value.get("evidence"), "Question " + name)
                require(text(value.get("reason")), "Question reason required")
                if answer == "UNKNOWN":
                    require(text(value.get("limitation")), "UNKNOWN needs its observed limitation")
            else:
                frames.refs(value.get("evidence", []), "Question " + name, required=False)
            answers[name] = answer
        first, second = (answers[q] for q in QUESTIONS[kind])
        require(not (first == "NO" and second == "YES"), f"Contradictory {kind} answers")
        if kind == "E02":
            require(
                not (interval.get("truncated_start") and first == "YES")
                and not (interval.get("truncated_end") and second == "YES"),
                "Truncated E02 cannot show the cut-off approach/withdrawal",
            )
        alternative = event.get("alternative_action")
        require(kind != "E02" or alternative is not None, "E02 needs alternative_action")
        if alternative is not None:
            status = alternative.get("status")
            require(status in ALTERNATIVE_STATUSES or (status is None and not complete), "Alt status")
            if status is not None:
                require(
                    (alternative.get("type") is None)
                    if status == "NONE"
                    else alternative.get("type") in ALTERNATIVE_TYPES,
                    "Alternative type must match its status",
                )
                frames.refs(alternative.get("evidence"), "Alternative action")
                require(text(alternative.get("reason")), "Alternative reason required")
        events[eid] = event

    relations = set()
    for relation in block["relations"]:
        src, dst = relation.get("from_event_id"), relation.get("to_event_id")
        require(src in events and dst in events and src != dst, "Relation events must exist")
        require(frozenset((src, dst)) not in relations, "Duplicate relation")
        relations.add(frozenset((src, dst)))
        kind = relation.get("relation")
        require(kind in RELATIONS and text(relation.get("reason")), "Relation type/reason required")
        frames.refs(relation.get("evidence"), "Relation")
        a, b = events[src], events[dst]
        require(a["source_actor_id"] == b["source_actor_id"], "Relation crosses source actors")
        same = a["same_action_group_id"] == b["same_action_group_id"]
        require((kind == "SAME_ACTION") == same, "SAME_ACTION must match the action group")
        if kind == "INDEPENDENT_FOLLOWUP":
            require(spans[dst][0] > spans[src][1], "Independent follow-up must start after")

    def related(x, y):
        return next(
            (
                r["relation"]
                for r in block["relations"]
                if {r["from_event_id"], r["to_event_id"]} == {x, y}
            ),
            None,
        )

    def drinking(event):
        # An E03 candidate or a shared group is not drinking; only its own answers decide.
        answers = [event["questions"][q]["answer"] for q in QUESTIONS["E03"]]
        return {("NO", "NO"): "REJECTED", ("YES", "YES"): "OBSERVED"}.get(
            tuple(answers), "UNRESOLVED"
        )

    for eid, event in events.items():
        if event["event_type"] != "E02" or not complete:
            continue
        for oid, other in events.items():
            if other["event_type"] != "E03" or other["source_actor_id"] != event["source_actor_id"]:
                continue
            if other["same_action_group_id"] == event["same_action_group_id"]:
                # A group is one physical action, not time proximity or a whole episode.
                require(
                    related(eid, oid) == "SAME_ACTION",
                    "Same-group E02/E03 need an explicit SAME_ACTION relation",
                )
                alternative = event["alternative_action"]
                if drinking(other) == "OBSERVED":
                    require(
                        alternative.get("type") == "DRINKING"
                        and alternative.get("status") in {"OBSERVED", "UNRESOLVED"},
                        "Same-action verified drinking must be recorded as the E02 alternative",
                    )
                elif drinking(other) == "UNRESOLVED":
                    require(
                        alternative.get("status") != "NONE",
                        "An unrejected same-action E03 contradicts E02 alternative NONE",
                    )
            elif spans[eid][0] <= spans[oid][1] and spans[oid][0] <= spans[eid][1]:
                require(
                    related(eid, oid) == "UNRESOLVED",
                    "Overlapping E02/E03 need an explicit unresolved relation",
                )

    coverage = block["coverage"]
    require(set(coverage) == set(QUESTIONS), "Coverage needs E01/E02/E03 states")
    for kind, value in coverage.items():
        state = value.get("state")
        require(
            state in COVERAGE_STATES and (state != "NOT_EVALUATED" or not complete),
            "Invalid/unfinished coverage state",
        )
        present = any(e["event_type"] == kind for e in events.values())
        require(
            state == "NOT_EVALUATED" or (state == "PRESENT") == present,
            "Coverage state contradicts events",
        )
        if state in {"ABSENT", "NOT_COVERED", "UNKNOWN"}:
            require(text(value.get("reason")), "Coverage reason required")
            frames.refs(value.get("evidence"), "Coverage " + kind)
        else:
            frames.refs(value.get("evidence", []), "Coverage " + kind, required=False)
        require(state != "ABSENT" or full, "ABSENT needs full observation of the clip")

    def blocks(eid, oid):
        # Only a competing explanation of this E02 blocks it: an unrejected E03 or another
        # E02. Auxiliary E01 relations and independent follow-ups never refute it.
        if related(eid, oid) not in {"SAME_ACTION", "UNRESOLVED"}:
            return False
        other = events[oid]["event_type"]
        return other == "E02" or (other == "E03" and drinking(events[oid]) != "REJECTED")

    def adequate(event):
        # Both questions YES are the evidence; alternative NONE is only a precondition.
        return (
            event["event_type"] == "E02"
            and all(v["answer"] == "YES" for v in event["questions"].values())
            and event["alternative_action"]["status"] == "NONE"
            and not any(blocks(event["event_id"], oid) for oid in events)
        )

    ids = set()
    for scope in block["scope_assessments"]:
        no_runtime_ids(scope, "Scope")
        sid = identifier(scope.get("assessment_id"))
        require(sid not in ids, "Duplicate assessment")
        ids.add(sid)
        require(scope.get("scope") in SCOPES, "Scope must be CLIP or FULL_SESSION")
        actor = scope.get("source_actor_id")
        require(actor in actors, "Scope actor is not a declared source actor")
        a, b = frames.span(scope.get("range"), "Analysis range")
        status = scope.get("status")
        require(status in SCOPE_STATUSES, "Invalid scope status")
        basis_ids = scope.get("basis_event_ids")
        # unresolved_conflicts bear on this scope's intake judgment; side issues that do not
        # change it go to auxiliary_open_issues. The author chooses; contradictions are refused.
        conflicts = scope.get("unresolved_conflicts")
        auxiliary = scope.get("auxiliary_open_issues")
        require(
            isinstance(basis_ids, list)
            and set(basis_ids) <= set(events)
            and len(set(basis_ids)) == len(basis_ids)
            and isinstance(conflicts, list)
            and all(text(v) for v in conflicts)
            and isinstance(auxiliary, list)
            and all(text(v) for v in auxiliary),
            "Unknown basis event or invalid conflicts/auxiliary issues",
        )
        require(
            scope.get("claims_actual_swallowing_or_prescription_match") is False,
            "Video evidence cannot claim swallowing or prescription match",
        )
        require(status == "NOT_EVALUATED" or text(scope.get("reason")), "Scope reason required")
        # Same source timeline, END = last included frame: start <= scope_end and end >= start.
        crossing = [
            e for e in events.values()
            if e["source_actor_id"] == actor
            and spans[e["event_id"]][0] <= b
            and spans[e["event_id"]][1] >= a
        ]
        for eid in basis_ids:
            ea, eb = spans[eid]
            # A positive judgment's basis lies wholly inside; other cited events must cross.
            require(
                events[eid] in crossing and (status != "CONFIRMED" or (a <= ea and eb <= b)),
                "Basis event outside the assessed actor/range",
            )
        boundaries = scope.get("session_boundaries")
        if scope["scope"] == "FULL_SESSION":
            require(
                isinstance(boundaries, dict) and text(boundaries.get("reason")),
                "FULL_SESSION needs verified session start/end evidence",
            )
            for key in ("start_evidence", "end_evidence"):
                for i in frames.refs(boundaries.get(key), "Session boundary"):
                    require(a <= i <= b, "Session boundary outside range")
            require(
                not any(
                    e["interval"].get("truncated_start") or e["interval"].get("truncated_end")
                    for e in crossing
                ),
                "Truncated actions cannot form a FULL_SESSION",
            )
        else:
            require(boundaries is None, "CLIP assessment has no session boundaries")
        if status == "CONFIRMED":
            require(not conflicts, "Unresolved conflicts prevent confirmation")
            require(
                basis_ids and all(adequate(events[eid]) for eid in basis_ids),
                "Confirmation needs adequate E02 basis only; E01/E03 cannot confirm intake",
            )
        elif status == "NOT_OBSERVED":
            require(full and not basis_ids, "NOT_OBSERVED needs full observation, no basis")
            require(not conflicts, "Unresolved conflicts prevent NOT_OBSERVED; record UNCERTAIN")
            e02 = [e for e in crossing if e["event_type"] == "E02"]
            require(
                all(
                    e["questions"]["intake_motion_visible"]["answer"] == "NO"
                    and "UNKNOWN" not in {v["answer"] for v in e["questions"].values()}
                    and e["alternative_action"]["status"] == "OBSERVED"
                    for e in e02
                ),
                "Unresolved E02 candidates prevent NOT_OBSERVED",
            )
            require(e02 or coverage["E02"]["state"] == "ABSENT", "NOT_OBSERVED needs E02 ABSENT")
        elif status == "NOT_EVALUATED":
            require(not basis_ids and not conflicts, "Unwritten assessment has no outcome")
    require(not complete or ids, "Scope assessment (or explicit NOT_EVALUATED) required")
    return seen
