"""Synthetic-only checks for the versioned behavior semantic profile (no real media/labels)."""

import copy
import hashlib
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from build_synthetic import fixture

from medication_video_annotation.authoring import AuthorSession
from medication_video_annotation.behavior_profile import PROFILE, profile_of
from medication_video_annotation.demo import create_demo
from medication_video_annotation.export_format import bundle_bytes
from medication_video_annotation.io import atomic_json, file_hash, read
from medication_video_annotation.legacy_record import ANALYSIS_FIELDS
from medication_video_annotation.records import (
    instantiate,
    record_hash,
    save_revision,
    validate_draft,
)
from medication_video_annotation.server import Workbench, make_server
from medication_video_annotation.workflow import begin_profile, complete_review, read_release

SOURCE = b"SYNTHETIC VFR FIXTURE - NOT A REAL VIDEO\n"
# 1/90000 time base, nonzero first PTS and variable frame gaps (fractional ms).
PTS = [90000, 94500, 97875, 102375, 106875, 111375, 115875, 120375, 124875, 129375, 133875,
       138375]
AT = "2026-10-27T00:00:00+09:00"


def vfr(root, sid="SYNTH-VFR"):
    base, stem = "data/SYNTHETIC/합성_의미시험", "합성 가변프레임_" + sid
    frames = [
        {
            "frame_index": i,
            "pts": pts,
            "time_base": "1/90000",
            "source_ms": pts / 90,
            "player_elapsed_ms": pts / 90 - PTS[0] / 90,
            "image_path": f"{base}/frames/{stem}/frame_{i:06d}.png",
        }
        for i, pts in enumerate(PTS)
    ]
    source = f"{base}/videos/{stem}.mp4"
    mapping = {
        "format": "onc110-frame-map-v0.1",
        "video_id": sid,
        "source_sha256": hashlib.sha256(SOURCE + sid.encode()).hexdigest(),
        "source_path": source,
        "width": 640,
        "height": 360,
        "frame_count": len(PTS),
        "first_source_ms": PTS[0] / 90,
        "frames": frames,
        "generation": {
            "sampling": "EVERY_SOURCE_FRAME",
            "resize": False,
            "decoder_version": "SYNTHETIC-NOT-DECODED",
            "tool_version": "synthetic-semantic-profile-test",
        },
    }
    item = {
        "format": "onc110-dataset-index-entry-v0.1",
        "video_id": sid,
        "source_sha256": mapping["source_sha256"],
        "source_path": source,
        "original_filename": stem + ".mp4",
        "classification": {"tier": "SYNTHETIC", "behavior": "합성_의미시험"},
        "frame_map_ref": f"{base}/frames/{stem}/frame-map.json",
        "record_dir": f"records/{sid}",
        "source_lineage": {"origin": "SYNTHETIC", "aliases": [], "original_path": None},
    }
    (root / source).parent.mkdir(parents=True, exist_ok=True)
    (root / source).write_bytes(SOURCE + sid.encode())
    for frame in frames:
        image = root / frame["image_path"]
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"SYNTHETIC NOT A REAL IMAGE")
    atomic_json(root / item["frame_map_ref"], mapping)
    return item, mapping


def ref(mapping, i):
    return {k: mapping["frames"][i][k] for k in ("frame_index", "pts", "time_base", "source_ms")}


def span(mapping, a, b):
    return {"start": ref(mapping, a), "end_last_included": ref(mapping, b)}


def log(mapping, input_set="SYNTH-INPUT-VFR", last=None):
    last = len(mapping["frames"]) - 1 if last is None else last
    return [
        {
            "kind": "AI_VISUAL_OBSERVATION",
            "observer_ref": "SYNTHETIC_TEST_NOT_REAL_OBSERVATION",
            "recorded_at_kst": AT,
            "input_set_id": input_set,
            "start_frame_index": 0,
            "end_frame_index": last,
            "display_width": mapping["width"],
            "display_height": mapping["height"],
            "note": "합성 시험용 관찰 기록. 실제 관찰 아님.",
        }
    ]


def fill_analysis(record, mapping, input_set="SYNTH-ANALYSIS"):
    record["analysis"].update(status="DRAFT", analyst="synthetic", model_identifier="NOT_OBSERVED")
    record["analysis"]["observation_log"] = log(mapping, input_set)
    for key in ANALYSIS_FIELDS:
        record["analysis"]["categories"][key] = {
            "status": "DRAFT",
            "observations": [
                {"basis": "DIRECT_OBSERVATION", "note": "합성 설명", "frame_indices": [0]}
            ],
        }


def q(mapping, answer, frames=(2, 5), limitation=None):
    value = {
        "answer": answer,
        "evidence": [ref(mapping, i) for i in frames],
        "reason": "합성 근거; 실제 판단 아님",
        "limitation": limitation,
    }
    if answer == "UNKNOWN" and limitation is None:
        value["limitation"] = "합성 가림"
    return value


def event(mapping, eid, kind, a, b, answers, group, *, alt="NONE", alt_type=None, actor="actor-1"):
    names = {
        "E01": ("handling_visible", "contents_removed_visible"),
        "E02": ("hand_to_mouth_visible", "intake_motion_visible"),
        "E03": ("container_reaches_mouth", "drinking_motion_visible"),
    }[kind]
    value = {
        "event_id": eid,
        "event_type": kind,
        "source_actor_id": actor,
        "same_action_group_id": group,
        "object_id": None,
        "interval": {
            **span(mapping, a, b),
            "boundary_status": "APPROXIMATE",
            "truncated_start": None,
            "truncated_end": None,
        },
        "key_moments": [
            {"role": "START", "frame": ref(mapping, a), "note": "합성"},
            {"role": "END_LAST_INCLUDED", "frame": ref(mapping, b), "note": "합성"},
        ],
        "questions": {n: q(mapping, v, (a, b)) for n, v in zip(names, answers)},
    }
    if kind == "E02":
        value["alternative_action"] = {
            "status": alt,
            "type": alt_type,
            "evidence": [ref(mapping, a), ref(mapping, b)],
            "reason": "합성 대안 판단",
            "limitation": None,
        }
    return value


def coverage(mapping, states):
    return {
        kind: {
            "state": state,
            "reason": None if state == "PRESENT" else "합성 관측 상태",
            "evidence": [] if state == "PRESENT" else [ref(mapping, 0), ref(mapping, 11)],
        }
        for kind, state in zip(("E01", "E02", "E03"), states)
    }


def scope(mapping, status, basis=(), *, kind="CLIP", boundaries=None, conflicts=()):
    return {
        "assessment_id": "SCOPE-1",
        "scope": kind,
        "source_actor_id": "actor-1",
        "range": span(mapping, 0, len(mapping["frames"]) - 1),
        "status": status,
        "basis_event_ids": list(basis),
        "unresolved_conflicts": list(conflicts),
        "auxiliary_open_issues": [],
        "reason": None if status == "NOT_EVALUATED" else "합성 범위 판단",
        "session_boundaries": boundaries,
        "claims_actual_swallowing_or_prescription_match": False,
    }


def new_record(item, mapping, events, states, scopes, relations=(), base=None):
    record = base or instantiate(item["video_id"], item["source_sha256"], item["frame_map_ref"])
    record = copy.deepcopy(record)
    if base is None:
        record = begin_profile(
            record, item, profile=PROFILE, revision_id=None, revision_file_sha256=None,
            actor="synthetic-author",
        )
        fill_analysis(record, mapping)
    block = record["label"]["behavior_annotation"]
    block["authoring"].update(
        prompt={"prompt_id": "synthetic-prompt", "sha256": hashlib.sha256(b"p").hexdigest()},
        input_set_ids=["SYNTH-INPUT-VFR"],
        raw_output={"status": "NOT_CAPTURED", "ref": None, "sha256": None,
                    "reason": "합성 시험: 모델 호출 없음"},
    )
    block["observation_log"] = log(mapping)
    block["actors"] = [
        {"source_actor_id": "actor-1", "description": "합성 대상", "runtime_track_links": []}
    ]
    block["events"] = list(events)
    block["coverage"] = coverage(mapping, states)
    block["scope_assessments"] = list(scopes)
    block["relations"] = list(relations)
    return record


def e02_only(item, mapping):
    return new_record(
        item, mapping,
        [event(mapping, "E02-1", "E02", 2, 6, ["YES", "YES"], "try-1")],
        ["NOT_COVERED", "PRESENT", "ABSENT"],
        [scope(mapping, "CONFIRMED", ["E02-1"])],
    )


@pytest.fixture
def root(tmp_path):
    return tmp_path / "합성 의미 프로필"


def save(root, item, mapping, record, base_hash=None):
    return save_revision(root, item, mapping, record, base_hash=base_hash, actor="synthetic")


def check(item, mapping, record):
    return validate_draft(record, {**mapping, "sample_id": item["video_id"]}, item["frame_map_ref"])


def test_e02_only_saves_rereads_preserves_extensions_and_skips_noop(root):
    item, mapping = vfr(root)
    record = e02_only(item, mapping)
    record["label"]["behavior_annotation"]["future_extension"] = {"kept": [1, None]}
    record["label"]["behavior_annotation"]["events"][0]["future_field"] = "kept"
    saved = save(root, item, mapping, record)
    path = Path(saved["path"])
    assert path.name == "revision-000000.json" and saved["status"] == "CREATED"
    reread = read(path)
    assert reread == saved["record"] and profile_of(reread) == PROFILE
    check(item, mapping, reread)
    original = path.read_bytes()
    block = reread["label"]["behavior_annotation"]
    assert block["future_extension"] == {"kept": [1, None]}
    assert block["events"][0]["future_field"] == "kept"
    # Original rational time is stored as given: fractional ms, nonzero PTS.
    assert block["events"][0]["interval"]["start"] == {
        "frame_index": 2, "pts": 97875, "time_base": "1/90000", "source_ms": 1087.5
    }
    # v0.1 questions are neither required nor fabricated for a new video.
    blank = instantiate(item["video_id"], item["source_sha256"], item["frame_map_ref"])
    assert {k: v for k, v in reread["label"].items() if k not in {
        "semantic_profile", "behavior_annotation"}} == blank["label"]
    assert reread["label"]["events"] == [] and reread["label"]["coverage"]["event_types"] == []
    assert block["review"]["human_review"]["status"] == "NOT_RUN"
    assert reread["analysis"]["status"] == "DRAFT"
    again = save(root, item, mapping, reread, saved["base_hash"])
    assert again["status"] == "UNCHANGED"
    edited = copy.deepcopy(reread)
    edited["label"]["behavior_annotation"]["events"][0]["questions"]["intake_motion_visible"][
        "reason"] = "합성 수정"
    second = save(root, item, mapping, edited, saved["base_hash"])
    assert Path(second["path"]).name == "revision-000001.json"
    assert second["record"]["revision_history"][-1]["previous_sha256"] == saved["base_hash"]
    assert second["record"]["label"]["behavior_annotation"]["future_extension"]
    assert path.read_bytes() == original
    lost = copy.deepcopy(second["record"])
    del lost["label"]["behavior_annotation"]["future_extension"]
    with pytest.raises(ValueError, match="Preserve existing"):
        save(root, item, mapping, lost, second["base_hash"])
    assert sorted(p.name for p in (root / "records" / item["video_id"]).iterdir()) == [
        "revision-000000.json", "revision-000001.json"]


@pytest.mark.parametrize(
    "case",
    ["e03-only", "e01-e03", "e01-only", "e02-ambiguous", "e02-alt-unresolved", "conflicts"],
)
def test_intake_confirmation_requires_adequate_e02(root, case):
    item, mapping = vfr(root)
    e01 = event(mapping, "E01-1", "E01", 0, 2, ["YES", "YES"], "prep-1")
    e03 = event(mapping, "E03-1", "E03", 7, 11, ["YES", "YES"], "drink-1")
    events, states, basis, conflicts = [], ["ABSENT", "ABSENT", "ABSENT"], [], []
    if case == "e03-only":
        events, states, basis = [e03], ["ABSENT", "ABSENT", "PRESENT"], ["E03-1"]
    elif case == "e01-e03":
        events, states, basis = [e01, e03], ["PRESENT", "ABSENT", "PRESENT"], ["E01-1", "E03-1"]
    elif case == "e01-only":
        events, states, basis = [e01], ["PRESENT", "ABSENT", "ABSENT"], ["E01-1"]
    else:
        alt = "UNRESOLVED" if case == "e02-alt-unresolved" else "NONE"
        answers = ["YES", "UNKNOWN"] if case == "e02-ambiguous" else ["YES", "YES"]
        e02 = event(mapping, "E02-1", "E02", 2, 6, answers, "try-1", alt=alt,
                    alt_type="FACE_TOUCH" if alt != "NONE" else None)
        events, states, basis = [e02], ["ABSENT", "PRESENT", "ABSENT"], ["E02-1"]
        conflicts = ["합성 상충"] if case == "conflicts" else []
    record = new_record(item, mapping, events, states,
                        [scope(mapping, "CONFIRMED", basis, conflicts=conflicts)])
    with pytest.raises(ValueError, match="E02|conflicts"):
        save(root, item, mapping, record)
    assert not (root / "records").exists()
    # The same evidence without a confirmation claim is a valid draft.
    status = "NOT_OBSERVED" if case in {"e03-only", "e01-e03", "e01-only"} else "UNCERTAIN"
    record["label"]["behavior_annotation"]["scope_assessments"] = [scope(mapping, status)]
    saved = save(root, item, mapping, record)
    assert saved["status"] == "CREATED" and read(Path(saved["path"])) == saved["record"]
    check(item, mapping, read(Path(saved["path"])))


def test_same_action_alternative_versus_independent_followup(root):
    item, mapping = vfr(root)
    e02 = event(mapping, "E02-1", "E02", 1, 5, ["YES", "YES"], "try-1")
    e03 = event(mapping, "E03-1", "E03", 7, 11, ["YES", "YES"], "drink-2")
    follow = {"from_event_id": "E02-1", "to_event_id": "E03-1", "relation": "INDEPENDENT_FOLLOWUP",
              "reason": "합성: 컵을 새로 듦", "evidence": [ref(mapping, 6), ref(mapping, 7)]}
    states = ["ABSENT", "PRESENT", "PRESENT"]
    ok = new_record(item, mapping, [e02, e03], states,
                    [scope(mapping, "CONFIRMED", ["E02-1"])], [follow])
    check(item, mapping, ok)  # A later independent drink does not refute E02.
    bad = copy.deepcopy(ok)
    bad["label"]["behavior_annotation"]["relations"][0]["evidence"] = []
    with pytest.raises(ValueError, match="observed evidence"):
        check(item, mapping, bad)
    bad = copy.deepcopy(ok)
    bad["label"]["behavior_annotation"]["events"][1]["interval"].update(start=ref(mapping, 4))
    bad["label"]["behavior_annotation"]["events"][1]["key_moments"][0]["frame"] = ref(mapping, 4)
    with pytest.raises(ValueError, match="follow-up must start after"):
        check(item, mapping, bad)
    bad = copy.deepcopy(ok)
    bad["label"]["behavior_annotation"]["relations"][0]["relation"] = "SAME_ACTION"
    with pytest.raises(ValueError, match="action group"):
        check(item, mapping, bad)
    # The drinking explanation of the SAME action is an E02 alternative, not support.
    same = copy.deepcopy(ok)
    block = same["label"]["behavior_annotation"]
    block["events"][1]["same_action_group_id"] = "try-1"
    block["relations"] = [{**follow, "relation": "SAME_ACTION", "reason": "합성: 같은 동작"}]
    with pytest.raises(ValueError, match="E02 alternative"):
        check(item, mapping, same)
    block["events"][0]["alternative_action"].update(status="OBSERVED", type="DRINKING")
    with pytest.raises(ValueError, match="adequate E02"):
        check(item, mapping, same)
    block["scope_assessments"] = [scope(mapping, "UNCERTAIN")]
    check(item, mapping, same)
    # Overlap with another group needs an explicit unresolved relation.
    overlap = copy.deepcopy(ok)
    block = overlap["label"]["behavior_annotation"]
    block["events"][1]["interval"].update(start=ref(mapping, 3))
    block["events"][1]["key_moments"][0]["frame"] = ref(mapping, 3)
    block["relations"] = []
    with pytest.raises(ValueError, match="Overlapping"):
        check(item, mapping, overlap)
    block["relations"] = [{**follow, "relation": "UNRESOLVED"}]
    with pytest.raises(ValueError, match="adequate E02"):
        check(item, mapping, overlap)


def test_occlusion_unknown_zero_events_and_unwritten_states(root):
    item, mapping = vfr(root)
    occluded = event(mapping, "E02-1", "E02", 2, 6, ["YES", "UNKNOWN"], "try-1",
                     alt="UNRESOLVED", alt_type="FACE_TOUCH")
    record = new_record(item, mapping, [occluded], ["ABSENT", "PRESENT", "ABSENT"],
                        [scope(mapping, "UNCERTAIN")])
    check(item, mapping, record)
    for status in ("CONFIRMED", "NOT_OBSERVED"):
        bad = copy.deepcopy(record)
        bad["label"]["behavior_annotation"]["scope_assessments"] = [
            scope(mapping, status, ["E02-1"] if status == "CONFIRMED" else [])
        ]
        with pytest.raises(ValueError, match="E02"):
            check(item, mapping, bad)
    bad = copy.deepcopy(record)
    bad["label"]["behavior_annotation"]["events"][0]["questions"]["intake_motion_visible"][
        "limitation"] = None
    with pytest.raises(ValueError, match="limitation"):
        check(item, mapping, bad)
    for unfinished in ("answer", "coverage", "alternative"):
        bad = copy.deepcopy(record)
        block = bad["label"]["behavior_annotation"]
        if unfinished == "answer":
            block["events"][0]["questions"]["hand_to_mouth_visible"]["answer"] = None
        elif unfinished == "coverage":
            block["coverage"]["E01"] = {"state": "NOT_EVALUATED", "reason": None, "evidence": []}
        else:
            block["events"][0]["alternative_action"]["status"] = None
        with pytest.raises(ValueError, match="missing answer|unfinished|Alt status"):
            check(item, mapping, bad)
    # An unwritten scope judgment is an explicit state, not an outcome.
    unwritten = copy.deepcopy(record)
    unwritten["label"]["behavior_annotation"]["scope_assessments"] = [
        scope(mapping, "NOT_EVALUATED")
    ]
    check(item, mapping, unwritten)
    # Zero events: absence with sufficient observation, never fabricated NO answers.
    zero = new_record(item, mapping, [], ["ABSENT", "ABSENT", "ABSENT"],
                      [scope(mapping, "NOT_OBSERVED")])
    check(item, mapping, zero)
    for change, message in (
        (lambda b: b["coverage"]["E02"].update(state="NOT_COVERED"), "E02 ABSENT"),
        (lambda b: b["coverage"]["E01"].update(evidence=[]), "observed evidence"),
        (lambda b: b["coverage"]["E03"].update(state="PRESENT"), "contradicts"),
        (lambda b: b["coverage"]["E03"].update(reason=" "), "reason"),
    ):
        bad = copy.deepcopy(zero)
        change(bad["label"]["behavior_annotation"])
        with pytest.raises(ValueError, match=message):
            check(item, mapping, bad)
    real = copy.deepcopy(record)
    real["label"]["behavior_annotation"]["coverage"]["E02"]["state"] = "ABSENT"
    with pytest.raises(ValueError, match="contradicts"):
        check(item, mapping, real)


def test_truncated_clip_and_full_session_rules(root):
    item, mapping = vfr(root)
    cut = event(mapping, "E02-1", "E02", 8, 11, ["YES", "YES"], "try-1")
    cut["interval"]["truncated_end"] = True
    record = new_record(item, mapping, [cut], ["ABSENT", "PRESENT", "ABSENT"],
                        [scope(mapping, "UNCERTAIN")])
    with pytest.raises(ValueError, match="Truncated E02"):
        check(item, mapping, record)
    cut["questions"]["intake_motion_visible"] = q(mapping, "UNKNOWN", (8, 11), "클립 끝에서 잘림")
    cut["alternative_action"].update(status="UNRESOLVED", type="OTHER")
    record["label"]["behavior_annotation"]["events"] = [cut]
    check(item, mapping, record)
    # A CLIP's limits do not become a FULL_SESSION statement.
    boundaries = {"start_evidence": [ref(mapping, 0)], "end_evidence": [ref(mapping, 11)],
                  "reason": "합성 세션 경계"}
    for value, message in ((None, "FULL_SESSION needs"), (boundaries, "Truncated actions")):
        bad = copy.deepcopy(record)
        bad["label"]["behavior_annotation"]["scope_assessments"] = [
            scope(mapping, "UNCERTAIN", kind="FULL_SESSION", boundaries=value)
        ]
        with pytest.raises(ValueError, match=message):
            check(item, mapping, bad)
    zero = new_record(item, mapping, [], ["ABSENT", "ABSENT", "ABSENT"],
                      [scope(mapping, "NOT_OBSERVED", kind="FULL_SESSION", boundaries=boundaries)])
    check(item, mapping, zero)
    clip = copy.deepcopy(zero)
    clip["label"]["behavior_annotation"]["scope_assessments"][0]["scope"] = "CLIP"
    with pytest.raises(ValueError, match="CLIP assessment"):
        check(item, mapping, clip)


CORE = "이 범위의 복약 가능성이 해결되지 않음"


def ranged(mapping, status, a, b, basis=(), conflicts=(), auxiliary=()):
    value = scope(mapping, status, basis, conflicts=conflicts)
    value.update(range=span(mapping, a, b), auxiliary_open_issues=list(auxiliary))
    return value


def test_core_conflicts_block_not_observed_but_auxiliary_issues_do_not(root):
    item, mapping = vfr(root)
    absent = ["ABSENT", "ABSENT", "ABSENT"]
    # Counterexample accepted before the fix: zero events, full observation, core conflict.
    bad = new_record(item, mapping, [], absent,
                     [ranged(mapping, "NOT_OBSERVED", 0, 11, conflicts=[CORE])])
    with pytest.raises(ValueError, match="conflicts prevent NOT_OBSERVED"):
        save(root, item, mapping, bad)
    assert not (root / "records").exists()
    for status in ("NOT_EVALUATED", "CONFIRMED"):
        other = copy.deepcopy(bad)
        other["label"]["behavior_annotation"]["scope_assessments"][0]["status"] = status
        with pytest.raises(ValueError, match="no outcome|conflicts|E02"):
            check(item, mapping, other)
    # Recorded as UNCERTAIN, the conflict and its reason survive save/reread unchanged.
    kept = copy.deepcopy(bad)
    kept["label"]["behavior_annotation"]["scope_assessments"][0]["status"] = "UNCERTAIN"
    saved = save(root, item, mapping, kept)
    reread = read(Path(saved["path"]))
    assert reread == saved["record"]
    assert reread["label"]["behavior_annotation"]["scope_assessments"] == kept["label"][
        "behavior_annotation"]["scope_assessments"]
    # A side issue that does not bear on the intake judgment is kept apart from conflicts.
    side = new_record(item, mapping, [], absent, [ranged(
        mapping, "NOT_OBSERVED", 0, 11, auxiliary=["E01 포장재 종류 판독 불가(판단과 무관)"])])
    check(item, mapping, side)
    for value in (None, [" "], "문자열"):
        broken = copy.deepcopy(side)
        broken["label"]["behavior_annotation"]["scope_assessments"][0][
            "auxiliary_open_issues"] = value
        with pytest.raises(ValueError, match="auxiliary"):
            check(item, mapping, broken)


# (scope range, unresolved E02-A interval, rejected E02-B interval, E02-A actor, accepted)
CROSSING = {
    "starts-before": ((4, 11), (2, 6), (8, 10), "actor-1", False),
    "ends-after": ((4, 8), (7, 10), (4, 5), "actor-1", False),
    "encloses-scope": ((4, 8), (2, 10), (5, 7), "actor-1", False),
    "end-equals-scope-start": ((4, 11), (2, 4), (8, 10), "actor-1", False),
    "start-equals-scope-end": ((4, 8), (8, 10), (5, 6), "actor-1", False),
    "adjacent-outside": ((4, 11), (0, 3), (8, 10), "actor-1", True),
    "other-actor": ((4, 11), (2, 6), (8, 10), "actor-2", True),
}


@pytest.mark.parametrize("case", sorted(CROSSING))
def test_absence_checks_every_event_crossing_the_scope(root, case):
    item, mapping = vfr(root)
    (a, b), (ua, ub), (ra, rb), actor, accepted = CROSSING[case]
    events = [
        event(mapping, "E02-A", "E02", ua, ub, ["YES", "UNKNOWN"], "try-a",
              alt="UNRESOLVED", alt_type="OTHER", actor=actor),
        event(mapping, "E02-B", "E02", ra, rb, ["NO", "NO"], "try-b",
              alt="OBSERVED", alt_type="FACE_TOUCH"),
    ]
    record = new_record(item, mapping, events, ["ABSENT", "PRESENT", "ABSENT"],
                        [ranged(mapping, "NOT_OBSERVED", a, b)])
    record["label"]["behavior_annotation"]["actors"].append(
        {"source_actor_id": "actor-2", "description": "합성 다른 대상", "runtime_track_links": []})
    if accepted:
        saved = save(root, item, mapping, record)
        assert read(Path(saved["path"])) == saved["record"]
        return
    with pytest.raises(ValueError, match="Unresolved E02 candidates prevent NOT_OBSERVED"):
        save(root, item, mapping, record)
    assert not (root / "records").exists()
    # UNCERTAIN may cite the crossing event; its original boundaries are stored untrimmed.
    block = record["label"]["behavior_annotation"]
    block["scope_assessments"] = [ranged(mapping, "UNCERTAIN", a, b, ["E02-A"], [
        "E02-A가 범위와 교차해 섭취형 동작을 판단할 수 없음"])]
    saved = save(root, item, mapping, record)
    interval = read(Path(saved["path"]))["label"]["behavior_annotation"]["events"][0]["interval"]
    assert (interval["start"], interval["end_last_included"]) == (ref(mapping, ua),
                                                                   ref(mapping, ub))


def test_positive_basis_stays_inside_while_other_checks_use_crossing(root):
    item, mapping = vfr(root)
    whole = event(mapping, "E02-1", "E02", 2, 6, ["YES", "YES"], "try-1")
    states = ["ABSENT", "PRESENT", "ABSENT"]
    record = new_record(item, mapping, [whole], states,
                        [ranged(mapping, "CONFIRMED", 4, 11, ["E02-1"])])
    with pytest.raises(ValueError, match="Basis event outside"):
        check(item, mapping, record)  # Crossing is not enough for a positive judgment.
    record["label"]["behavior_annotation"]["scope_assessments"] = [
        ranged(mapping, "CONFIRMED", 2, 6, ["E02-1"])]
    check(item, mapping, record)  # Inclusive END: [2,6] lies inside [2,6].
    record["label"]["behavior_annotation"]["scope_assessments"] = [
        ranged(mapping, "UNCERTAIN", 7, 11, ["E02-1"])]
    with pytest.raises(ValueError, match="Basis event outside"):
        check(item, mapping, record)  # Cited events must at least cross the range.
    cut = event(mapping, "E02-1", "E02", 2, 6, ["UNKNOWN", "UNKNOWN"], "try-1",
                alt="UNRESOLVED", alt_type="OTHER")
    cut["interval"]["truncated_start"] = True
    boundaries = {"start_evidence": [ref(mapping, 4)], "end_evidence": [ref(mapping, 11)],
                  "reason": "합성 세션 경계"}
    session = ranged(mapping, "UNCERTAIN", 4, 11)
    session.update(scope="FULL_SESSION", session_boundaries=boundaries)
    record = new_record(item, mapping, [cut], states, [session])
    with pytest.raises(ValueError, match="Truncated actions"):
        check(item, mapping, record)  # Started before 4: crosses the session start.


def same_action(item, mapping, e02_answers, e03_answers, *, alt="NONE", alt_type=None,
                status="CONFIRMED", group="g1", relation="SAME_ACTION", e03_span=(2, 6)):
    events = [
        event(mapping, "E02-1", "E02", 2, 6, e02_answers, "g1", alt=alt, alt_type=alt_type),
        event(mapping, "E03-1", "E03", *e03_span, e03_answers, group),
    ]
    relations = [{"from_event_id": "E02-1", "to_event_id": "E03-1", "relation": relation,
                  "reason": "합성 관계", "evidence": [ref(mapping, 2), ref(mapping, 6)]}]
    basis = ["E02-1"] if status in {"CONFIRMED", "UNCERTAIN"} else []
    return new_record(item, mapping, events, ["ABSENT", "PRESENT", "PRESENT"],
                      [scope(mapping, status, basis)], relations)


YY, NN, YU, YN = ["YES", "YES"], ["NO", "NO"], ["YES", "UNKNOWN"], ["YES", "NO"]
# name: (kwargs, accepted or error fragment)
SAME_ACTION_CASES = {
    "rejected-e03-does-not-block": (dict(e02_answers=YY, e03_answers=NN), True),
    "clear-drinking-explains-e02": (dict(e02_answers=YN, e03_answers=YY, alt="OBSERVED",
                                         alt_type="DRINKING", status="NOT_OBSERVED"), True),
    "clear-drinking-not-intake-basis": (dict(e02_answers=YY, e03_answers=YY, alt="OBSERVED",
                                             alt_type="DRINKING"), "adequate E02"),
    "clear-drinking-vs-alt-none": (dict(e02_answers=YY, e03_answers=YY, status="UNCERTAIN"),
                                   "verified drinking"),
    "clear-drinking-vs-other-type": (dict(e02_answers=YY, e03_answers=YY, alt="OBSERVED",
                                          alt_type="FACE_TOUCH", status="UNCERTAIN"),
                                     "verified drinking"),
    "unresolved-e03-vs-alt-none": (dict(e02_answers=YY, e03_answers=YU, status="UNCERTAIN"),
                                   "contradicts E02 alternative NONE"),
    "unresolved-e03-blocks": (dict(e02_answers=YY, e03_answers=YU, alt="UNRESOLVED",
                                   alt_type="DRINKING"), "adequate E02"),
    "unresolved-e03-uncertain": (dict(e02_answers=YY, e03_answers=YU, alt="UNRESOLVED",
                                      alt_type="DRINKING", status="UNCERTAIN"), True),
    "same-group-needs-relation": (dict(e02_answers=YY, e03_answers=NN,
                                       relation="UNRESOLVED"), "action group"),
    "unresolved-relation-drinking-blocks": (dict(e02_answers=YY, e03_answers=YY, group="g2",
                                                 relation="UNRESOLVED", e03_span=(5, 9)),
                                            "adequate E02"),
    "unresolved-relation-drinking-uncertain": (dict(e02_answers=YY, e03_answers=YY,
                                                    group="g2", relation="UNRESOLVED",
                                                    e03_span=(5, 9), status="UNCERTAIN"), True),
    "unresolved-relation-rejected-e03": (dict(e02_answers=YY, e03_answers=NN, group="g2",
                                              relation="UNRESOLVED", e03_span=(5, 9)), True),
    "independent-followup-drink": (dict(e02_answers=YY, e03_answers=YY, group="g2",
                                        relation="INDEPENDENT_FOLLOWUP", e03_span=(7, 11)),
                                   True),
}


@pytest.mark.parametrize("case", sorted(SAME_ACTION_CASES))
def test_same_action_group_and_drinking_evidence(root, case):
    item, mapping = vfr(root)
    kwargs, expected = SAME_ACTION_CASES[case]
    record = same_action(item, mapping, **kwargs)
    if expected is True:
        saved = save(root, item, mapping, record)
        assert read(Path(saved["path"])) == saved["record"]
    else:
        with pytest.raises(ValueError, match=expected):
            save(root, item, mapping, record)
        assert not (root / "records").exists()


@pytest.mark.parametrize("relation", ["SAME_ACTION", "UNRESOLVED"])
def test_auxiliary_e01_relation_and_alternative_none_are_not_global_rules(root, relation):
    item, mapping = vfr(root)
    prep = event(mapping, "E01-1", "E01", 0, 3, ["YES", "YES"],
                 "try-1" if relation == "SAME_ACTION" else "prep-1")
    intake = event(mapping, "E02-1", "E02", 2, 6, ["YES", "YES"], "try-1")
    link = {"from_event_id": "E01-1", "to_event_id": "E02-1", "relation": relation,
            "reason": "합성: 꺼낸 뒤 입으로", "evidence": [ref(mapping, 2), ref(mapping, 3)]}
    record = new_record(item, mapping, [prep, intake], ["PRESENT", "PRESENT", "ABSENT"],
                        [scope(mapping, "CONFIRMED", ["E02-1"])], [link])
    check(item, mapping, record)  # E01 relations neither confirm nor refute E02.
    record["label"]["behavior_annotation"]["scope_assessments"] = [
        scope(mapping, "CONFIRMED", ["E01-1"])]
    with pytest.raises(ValueError, match="adequate E02"):
        check(item, mapping, record)
    # Alternative NONE is a precondition, never evidence of intake by itself.
    unseen = event(mapping, "E02-1", "E02", 2, 6, ["UNKNOWN", "UNKNOWN"], "try-1")
    record = new_record(item, mapping, [unseen], ["ABSENT", "PRESENT", "ABSENT"],
                        [scope(mapping, "CONFIRMED", ["E02-1"])])
    with pytest.raises(ValueError, match="adequate E02"):
        check(item, mapping, record)


def mutate(path, value):
    def apply(block):
        target = block
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
    return apply


E = ["events", 0]
BAD = {
    "rounded-vfr-ms": (mutate(E + ["interval", "start", "source_ms"], 1088), "differ"),
    "pts-off": (mutate(E + ["questions", "intake_motion_visible", "evidence", 0, "pts"], 97876),
                "differ"),
    "time-base": (mutate(E + ["key_moments", 0, "frame", "time_base"], "1/1000"), "differ"),
    "elapsed-as-pts": (mutate(E + ["interval", "start", "source_ms"], 87.5), "differ"),
    "outside-video": (mutate(E + ["interval", "end_last_included", "frame_index"], 99), "outside"),
    "unknown-actor": (mutate(E + ["source_actor_id"], "track-7"), "declared source actor"),
    "runtime-id-copy": (mutate(E + ["runtime_track_id"], "actor-1"), "runtime track"),
    "track-link-no-evidence": (mutate(["actors", 0, "runtime_track_links"], [{
        "stream_id": "cam-1", "target_track_id": "actor-1", "status": "UNCONFIRMED",
        "reason": "합성", "evidence": []}]), "observed evidence"),
    "track-confirmed-unreviewed": (mutate(["actors", 0, "runtime_track_links"], [{
        "stream_id": "cam-1", "target_track_id": "track-3", "status": "CONFIRMED",
        "reason": "합성", "evidence": [{"frame_index": 2, "pts": 97875, "time_base": "1/90000",
                                        "source_ms": 1087.5}]}]), "human review"),
    "moment-outside": (mutate(E + ["key_moments", 1, "role"], "START"), "START moment"),
    "legacy-e02-question": (mutate(E + ["questions", "object_is_medication"],
                                   {"answer": "YES"}), "v0.1 questions"),
    "clinical-claim": (mutate(["scope_assessments", 0,
                               "claims_actual_swallowing_or_prescription_match"], True),
                       "swallowing"),
    "evaluation": (mutate(["evaluation_eligible"], True), "evaluation truth"),
    "final-truth": (mutate(["final_ground_truth"], True), "evaluation truth"),
    "independent": (mutate(["review", "independent_review"], "COMPLETE"), "Independent"),
    "fake-review": (mutate(["review", "human_review", "status"], "REVIEWED"), "Human review"),
    "exact-draft": (mutate(E + ["interval", "boundary_status"], "EXACT"), "EXACT"),
    "guessed-model": (mutate(["authoring", "model_identifier"],
                             {"value": "gpt-astra", "confirmation": "UNCONFIRMED"}),
                      "runtime-confirmed"),
    "raw-ref-uncaptured": (mutate(["authoring", "raw_output", "ref"], "runs/x.txt"),
                           "no ref"),
    "missing-input-set": (mutate(["authoring", "input_set_ids"], []), "input sets"),
    "partial-observation": (mutate(["observation_log", 0, "end_frame_index"], 10),
                            "full observation|unobserved"),
    "prep-as-observation": (mutate(["observation_log", 0, "kind"], "DECODE_PREPARATION"),
                            "not visual observation"),
    "unsupported-profile": (mutate(["profile"], "onc110-behavior-annotation-v9"), "disagree"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_invalid_time_target_profile_and_promotion_rejected(root, case):
    item, mapping = vfr(root)
    record = e02_only(item, mapping)
    change, message = BAD[case]
    change(record["label"]["behavior_annotation"])
    with pytest.raises(ValueError, match=message):
        save(root, item, mapping, record)
    assert not (root / "records").exists()


def test_profile_declaration_and_map_requirements(root):
    item, mapping = vfr(root)
    record = e02_only(item, mapping)
    for change, message in (
        (lambda label: label.update(semantic_profile="behavior-vnext-prep-v0.1"), "Unsupported"),
        (lambda label: label.pop("semantic_profile"), "Unsupported"),
        (lambda label: label.update(semantic_profile=None), "Unsupported"),
        (lambda label: label.pop("behavior_annotation"), "disagree"),
        (lambda label: label["coverage"].update(annotation_status="DRAFT"), "v0.1 answers"),
    ):
        bad = copy.deepcopy(record)
        change(bad["label"])
        with pytest.raises(ValueError, match=message):
            save(root, item, mapping, bad)
    # Fake v0.1 E02 answers are not accepted to make a new-profile record pass.
    fake = copy.deepcopy(record)
    fake["label"]["events"] = [{
        "event_id": "OLD-E02", "event_type": "E02", "annotation_status": "DRAFT",
        "subject_key": None, "object_key": None, "interval": None, "context_ranges": [],
        "check_answers": {"object_is_medication": "UNKNOWN",
                          "object_transfer_into_mouth_visible": "UNKNOWN"},
        "evidence": [{"check": c, "frame_index": 2, "source_ms": 1087.5, "role": "OBSERVATION",
                      "note": "가짜 옛 답"} for c in ("object_is_medication",
                                                    "object_transfer_into_mouth_visible")],
        "limitations": [], "links": [], "reference_verification": None,
        "verification_rule_version": "fake",
    }]
    with pytest.raises(ValueError, match="v0.1 answers"):
        save(root, item, mapping, fake)
    assert not (root / "records").exists()
    # A map without original pts/time_base cannot carry the new profile.
    legacy_item, legacy_map, legacy = fixture()
    legacy_root = root.parent / "legacy-map"
    source = legacy_root / legacy_item["source_path"]
    source.parent.mkdir(parents=True)
    from build_synthetic import SOURCE_BYTES

    source.write_bytes(SOURCE_BYTES)
    for frame in legacy_map["frames"]:
        image = legacy_root / frame["image_path"]
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"x")
    atomic_json(legacy_root / legacy_item["frame_map_ref"], legacy_map)
    legacy["revision_history"] = []
    profiled = begin_profile(legacy, legacy_item, profile=PROFILE, revision_id=None,
                             revision_file_sha256=None, actor="synthetic")
    with pytest.raises(ValueError, match="rational pts"):
        validate_draft(
            profiled, {**legacy_map, "sample_id": legacy_item["video_id"]},
            legacy_item["frame_map_ref"],
        )
    # The v0.1 legacy record itself still saves unchanged.
    assert save(legacy_root, legacy_item, legacy_map, legacy)["status"] == "CREATED"


def test_raw_output_capture_must_exist_with_hash(root):
    item, mapping = vfr(root)
    record = e02_only(item, mapping)
    raw = root / "runs/synthetic/raw-output.txt"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"synthetic captured model output")
    captured = {"status": "CAPTURED", "ref": "runs/synthetic/raw-output.txt",
                "sha256": "0" * 64, "reason": None}
    record["label"]["behavior_annotation"]["authoring"]["raw_output"] = captured
    with pytest.raises(ValueError, match="Captured raw output"):
        save(root, item, mapping, record)
    captured["sha256"] = file_hash(raw)
    assert save(root, item, mapping, record)["status"] == "CREATED"


@pytest.fixture
def bench(tmp_path):
    root = tmp_path / "합성 의미 화면"
    create_demo(root)
    return Workbench(root, root / "runs/synthetic-workbench", ["DEMO-01", "DEMO-02"])


def demo_profile(bench, sid="DEMO-01", group="try-1"):
    data = bench.record(sid)
    mapping = bench.maps[sid]
    item = bench.items[sid]
    path = bench.root / item["record_dir"] / data["revision_id"]
    record = begin_profile(data["record"], item, profile=PROFILE,
                           revision_id=data["revision_id"], revision_file_sha256=file_hash(path),
                           actor="synthetic-author")
    last = mapping["frame_count"] - 1
    block = record["label"]["behavior_annotation"]
    block["basis"]["change_reason"] = "합성: 새 의미 기준 재관찰"
    block["authoring"].update(
        prompt={"prompt_id": "synthetic-prompt", "sha256": hashlib.sha256(b"p").hexdigest()},
        input_set_ids=["SYNTH-NEW-PROFILE"],
        raw_output={"status": "NOT_CAPTURED", "ref": None, "sha256": None,
                    "reason": "합성 시험: 모델 호출 없음"},
    )
    block["observation_log"] = log(mapping, "SYNTH-NEW-PROFILE")
    block["actors"] = [{"source_actor_id": "actor-1", "description": "합성 도형",
                        "runtime_track_links": []}]
    block["events"] = [event(mapping, "E02-1", "E02", 2, 6, ["YES", "YES"], group)]
    block["coverage"] = {
        "E01": {"state": "NOT_COVERED", "reason": "합성", "evidence": [ref(mapping, 0)]},
        "E02": {"state": "PRESENT", "reason": None, "evidence": []},
        "E03": {"state": "ABSENT", "reason": "합성", "evidence": [ref(mapping, last)]},
    }
    block["scope_assessments"] = [
        {**scope(mapping, "CONFIRMED", ["E02-1"]), "range": span(mapping, 0, last)}
    ]
    return record, data


def write(bench, record, data, status="DRAFT", reviewer=""):
    return bench.write("save", record["video_id"], {
        "record": copy.deepcopy(record), "base_hash": data["base_hash"],
        "actor_ref": "synthetic-editor", "review_status": status, "reviewer_ref": reviewer,
        "reviewed_ranges": [{"start_frame_index": 0, "end_frame_index": 15}]
        if status == "REVIEWED" else [],
    })


def test_existing_review_is_history_not_inherited_by_new_profile(bench):
    data = bench.record("DEMO-01")
    reviewed = write(bench, data["record"], data, "REVIEWED", "검수자 A")
    legacy_files = {p: file_hash(p) for p in (bench.root / "records/DEMO-01").glob("revision-*")}
    assert reviewed["record"]["label"]["coverage"]["annotation_status"] == "REVIEWED"
    record, data = demo_profile(bench)
    assert record["analysis"]["status"] == "DRAFT"
    for change, message in (
        (lambda r: r["label"]["events"][0]["evidence"][0].update(note="옛 근거 변경"),
         "read-only"),
        (lambda r: r["label"]["review"].update(reviewer_ref="누군가"), "Protected|read-only"),
        (lambda r: r["label"]["behavior_annotation"]["basis"]["base_revision"].update(
            record_sha256="0" * 64), "exact base"),
        (lambda r: (r["label"]["behavior_annotation"].update(
            observation_log=copy.deepcopy(r["analysis"]["observation_log"])),
            r["label"]["behavior_annotation"]["authoring"].update(
                input_set_ids=["SYNTHETIC-INPUT"])), "cannot be reused"),
        (lambda r: r["analysis"].update(status="REVIEWED"), "unreviewed draft|follow"),
    ):
        bad = copy.deepcopy(record)
        change(bad)
        with pytest.raises(ValueError, match=message):
            bench.write("save", "DEMO-01", {"record": bad, "base_hash": data["base_hash"],
                                            "actor_ref": "synthetic-author"})
    saved = bench.write("save", "DEMO-01", {"record": record, "base_hash": data["base_hash"],
                                            "actor_ref": "synthetic-author"})
    new = saved["record"]
    assert saved["revision_id"] == "revision-000002.json"
    assert new["revision_history"][-1]["operation"] == "SEMANTIC_PROFILE_START"
    assert new["label"]["coverage"]["annotation_status"] == "REVIEWED"  # history kept verbatim
    assert new["label"]["review"] == reviewed["record"]["label"]["review"]
    assert new["label"]["behavior_annotation"]["review"]["human_review"]["status"] == "NOT_RUN"
    assert new["analysis"]["status"] == "DRAFT"
    assert new["label"]["behavior_annotation"]["basis"]["base_revision"]["revision_id"] == (
        "revision-000001.json")
    assert {p: file_hash(p) for p in legacy_files} == legacy_files
    # No HUMAN_REVIEW invalidation is logged for the untouched v0.1 review.
    assert bench.timer_state("DEMO-01", "HUMAN_REVIEW")["state"] == "NOT_RUN"
    data = bench.record("DEMO-01")
    downgrade = copy.deepcopy(data["record"])
    del downgrade["label"]["semantic_profile"], downgrade["label"]["behavior_annotation"]
    with pytest.raises(ValueError, match="drop"):
        bench.write("save", "DEMO-01", {"record": downgrade, "base_hash": data["base_hash"],
                                        "actor_ref": "synthetic-author"})
    with pytest.raises(ValueError, match="unified save"):
        complete_review(bench.root, bench.items["DEMO-01"], bench.maps["DEMO-01"],
                        base_hash=data["base_hash"], actor="x",
                        reviewed_ranges=[{"start_frame_index": 0, "end_frame_index": 15}])


def test_workbench_save_refuses_r1_r2_without_partial_revision(bench):
    record, data = demo_profile(bench)
    block = record["label"]["behavior_annotation"]
    mapping, last = bench.maps["DEMO-01"], bench.maps["DEMO-01"]["frame_count"] - 1
    unresolved = event(mapping, "E02-1", "E02", 2, 6, ["YES", "UNKNOWN"], "try-1",
                       alt="UNRESOLVED", alt_type="OTHER")
    present = {"state": "PRESENT", "reason": None, "evidence": []}
    absent = {"state": "ABSENT", "reason": "합성", "evidence": [ref(mapping, last)]}
    before = sorted(p.name for p in (bench.root / "records/DEMO-01").iterdir())
    for events, e02_state, scope_value, message in (
        ([], absent, ranged(mapping, "NOT_OBSERVED", 0, last, conflicts=[CORE]),
         "conflicts prevent"),
        ([unresolved], present, ranged(mapping, "NOT_OBSERVED", 4, last),
         "Unresolved E02 candidates"),
    ):
        block.update(events=events, scope_assessments=[scope_value])
        block["coverage"]["E02"] = e02_state
        with pytest.raises(ValueError, match=message):
            bench.write("save", "DEMO-01", {"record": copy.deepcopy(record),
                                            "base_hash": data["base_hash"],
                                            "actor_ref": "synthetic-author"})
        assert sorted(p.name for p in (bench.root / "records/DEMO-01").iterdir()) == before
    block["scope_assessments"] = [ranged(mapping, "UNCERTAIN", 4, last, ["E02-1"], [CORE])]
    saved = bench.write("save", "DEMO-01", {"record": record, "base_hash": data["base_hash"],
                                            "actor_ref": "synthetic-author"})
    stored = bench.record("DEMO-01")["record"]["label"]["behavior_annotation"]
    assert sorted(p.name for p in (bench.root / "records/DEMO-01").iterdir()) == sorted(
        before + [saved["revision_id"]])
    assert stored["scope_assessments"] == block["scope_assessments"]
    assert stored["events"][0]["interval"]["start"]["frame_index"] == 2  # not trimmed to 4


def test_unified_save_review_cycle_and_refusals_over_http(bench):
    record, data = demo_profile(bench)
    first = bench.write("save", "DEMO-01", {"record": record, "base_hash": data["base_hash"],
                                            "actor_ref": "synthetic-author"})
    server = make_server(bench.root, bench.run.directory, ["DEMO-01", "DEMO-02"], 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        token = json.load(urlopen(base + "/api/index"))["token"]

        def post(action, value):
            request = Request(base + f"/api/{action}/DEMO-01", data=json.dumps(value).encode(),
                              headers={"Content-Type": "application/json",
                                       "X-Workbench-Token": token})
            try:
                return json.load(urlopen(request))
            except HTTPError as error:
                return {"http_error": error.code, **json.load(error)}

        loaded = json.load(urlopen(base + "/api/record/DEMO-01"))
        assert loaded["semantic_profile"] == PROFILE and loaded["revision_id"] == first[
            "revision_id"]
        assert loaded["record"] == first["record"]
        assert {"pts", "time_base"} <= set(loaded["frames"][0])
        edited = copy.deepcopy(loaded["record"])
        event_value = edited["label"]["behavior_annotation"]["events"][0]
        event_value["interval"]["boundary_status"] = "EXACT"
        value = {"record": edited, "base_hash": loaded["base_hash"], "actor_ref": "검수자 B",
                 "review_status": "REVIEWED", "reviewer_ref": "검수자 B",
                 "reviewed_ranges": [{"start_frame_index": 0, "end_frame_index": 15}]}
        reviewed = post("save", value)
        block = reviewed["record"]["label"]["behavior_annotation"]
        assert reviewed["revision_id"] == "revision-000002.json"
        assert block["review"]["human_review"]["status"] == "REVIEWED"
        assert block["review"]["human_review"]["reviewer_ref"] == "검수자 B"
        assert block["review"]["ai_review"]["status"] == "NOT_RUN"
        assert block["review"]["independent_review"] == "NOT_RUN"
        assert block["events"][0]["interval"]["boundary_status"] == "EXACT"
        assert reviewed["record"]["analysis"]["status"] == "REVIEWED"
        assert reviewed["record"]["label"]["coverage"]["annotation_status"] == "DRAFT"
        assert reviewed["record"]["label"]["review"] == first["record"]["label"]["review"]
        assert not block["final_ground_truth"] and not block["evaluation_eligible"]
        same = {**value, "record": reviewed["record"], "base_hash": reviewed["base_hash"]}
        assert post("save", same)["status"] == "UNCHANGED"
        tampered = copy.deepcopy(reviewed["record"])
        tampered["label"]["behavior_annotation"]["review"]["ai_review"]["status"] = "COMPLETE"
        refused = post("save", {**same, "record": tampered, "review_status": "DRAFT"})
        assert refused["http_error"] == 400 and "review" in refused["error"]
        draft = copy.deepcopy(reviewed["record"])
        draft["label"]["behavior_annotation"]["events"][0]["questions"]["intake_motion_visible"] = (
            q(bench.maps["DEMO-01"], "UNKNOWN", (2, 6), "합성 가림"))
        draft["label"]["behavior_annotation"]["events"][0]["alternative_action"].update(
            status="UNRESOLVED", type="FACE_TOUCH")
        draft["label"]["behavior_annotation"]["scope_assessments"][0].update(
            status="UNCERTAIN", basis_event_ids=[])
        redrafted = post("save", {**same, "record": draft, "review_status": "DRAFT",
                                  "reviewer_ref": "검수자 B", "reviewed_ranges": []})
        block = redrafted["record"]["label"]["behavior_annotation"]
        assert redrafted["revision_id"] == "revision-000003.json"
        assert block["review"]["human_review"]["status"] == "NOT_RUN"
        assert block["events"][0]["interval"]["boundary_status"] == "APPROXIMATE"
        assert redrafted["record"]["analysis"]["status"] == "DRAFT"
        exported = post("export", {"request": "USER_REVIEW_BUTTON", "actor_ref": "x",
                                   "revision_id": redrafted["revision_id"],
                                   "base_hash": redrafted["base_hash"]})
        assert exported["http_error"] == 400 and "not supported yet" in exported["error"]
        assert not (bench.root / "exports").exists()
        html = urlopen(base + "/").read().decode("utf-8")
        assert "renderBehaviorLabels" in html and "profileCheckNames" in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_unsupported_export_and_release_refuse_profile_records(bench):
    record, data = demo_profile(bench)
    saved = write(bench, record, data)
    path = Path(saved["path"])
    with pytest.raises(ValueError, match="not supported yet"):
        bundle_bytes(saved["record"], revision_ref="x", revision_file_sha256="0" * 64,
                     exported_at_kst=AT, purpose="TEST")
    item, mapping_path = bench.items["DEMO-01"], bench.root / bench.items["DEMO-01"][
        "frame_map_ref"]
    release = {"format": "onc110-release-v0.1", "entries": [{
        "video_id": "DEMO-01",
        "source_path": item["source_path"], "source_sha256": item["source_sha256"],
        "frame_map_ref": item["frame_map_ref"], "frame_map_sha256": file_hash(mapping_path),
        "revision_ref": path.relative_to(bench.root).as_posix(),
        "revision_file_sha256": file_hash(path),
    }]}
    with pytest.raises(ValueError, match="Release reader v0.1"):
        read_release(bench.root, release)
    legacy = bench.root / "records/DEMO-02/revision-000000.json"
    release["entries"][0].update(
        video_id="DEMO-02", **{k: bench.items["DEMO-02"][k] for k in (
            "source_path", "source_sha256", "frame_map_ref")},
        frame_map_sha256=file_hash(bench.root / bench.items["DEMO-02"]["frame_map_ref"]),
        revision_ref="records/DEMO-02/revision-000000.json", revision_file_sha256=file_hash(legacy),
    )
    assert len(read_release(bench.root, release)) == 1  # v0.1 reading unchanged


def test_author_checkpoint_resume_and_profile_mismatch(bench):
    root, run = bench.root, bench.run.directory
    legacy = AuthorSession(root, run, "DEMO-02", "synthetic-ai")
    legacy.dispatch({"command": "checkpoint", "cursor": 3, "note": "v0.1 합성 중간"})
    author = AuthorSession(root, run, "DEMO-02", "synthetic-ai", PROFILE)
    state = author.dispatch({"command": "get"})
    assert state["semantic_profile"] == PROFILE and profile_of(state["record"]) == PROFILE
    assert state["record"]["label"]["behavior_annotation"]["basis"]["base_revision"][
        "revision_id"] == "revision-000000.json"
    assert state["record"]["label"]["behavior_annotation"]["authoring"]["model_identifier"] == {
        "value": None, "confirmation": "UNCONFIRMED"}
    with pytest.raises(ValueError, match="another semantic profile"):
        author.dispatch({"command": "checkpoint", "cursor": 4, "note": "partial"})
    mapping = bench.maps["DEMO-02"]
    block = ["label", "behavior_annotation"]
    author.dispatch({"command": "set", "path": block + ["observation_log"],
                     "value": log(mapping, "SYNTH-PARTIAL", last=5)})
    author.dispatch({"command": "set", "path": block + ["authoring", "input_set_ids"],
                     "value": ["SYNTH-PARTIAL"]})
    author.dispatch({"command": "set", "path": block + ["actors"], "value": [
        {"source_actor_id": "actor-1", "description": "합성", "runtime_track_links": []}]})
    partial = event(mapping, "E02-1", "E02", 2, 4, ["YES", "YES"], "try-1")
    partial["questions"]["intake_motion_visible"] = {"answer": None, "evidence": [],
                                                     "reason": None, "limitation": None}
    author.dispatch({"command": "set", "path": block + ["events"], "value": [partial]})
    saved = author.dispatch({"command": "checkpoint", "cursor": 6, "note": "합성 중간",
                             "replace_reason": "새 의미 작업으로 교체"})
    checkpoint = saved["checkpoint"]
    assert checkpoint["format"] == "onc110-checkpoint-v0.2"
    assert checkpoint["semantic_profile"] == PROFILE
    assert checkpoint["base_revision_id"] == "revision-000000.json"
    assert checkpoint["observation_progress"] == {"observed_frames": 6, "frame_count": 16,
                                                  "cursor": 6}
    assert checkpoint["replaced_checkpoint"]["semantic_profile"] is None
    # Evidence beyond the observed frames is refused even in a checkpoint.
    ahead = copy.deepcopy(partial)
    ahead["questions"]["hand_to_mouth_visible"]["evidence"] = [ref(mapping, 9)]
    author.dispatch({"command": "set", "path": block + ["events"], "value": [ahead]})
    with pytest.raises(ValueError, match="unobserved"):
        author.dispatch({"command": "checkpoint", "cursor": 6, "note": "ahead"})
    with pytest.raises(ValueError, match="semantic profile differs"):
        AuthorSession(root, run, "DEMO-02", "synthetic-ai").dispatch({"command": "restore"})
    resumed = AuthorSession(root, run, "DEMO-02", "synthetic-ai", PROFILE)
    restored = resumed.dispatch({"command": "restore"})
    assert restored["cursor"] == 6
    assert restored["record"]["label"]["behavior_annotation"]["observation_log"] == log(
        mapping, "SYNTH-PARTIAL", last=5)
    # Incomplete work cannot be saved as a revision.
    resumed.dispatch({"command": "timer", "action": "START"})
    resumed.dispatch({"command": "timer", "action": "PAUSE", "reason": "EXTERNAL_WAIT"})
    with pytest.raises(ValueError, match="reason required|missing answer|full observation"):
        resumed.dispatch({"command": "save"})
    resumed.close()
    assert sorted(p.name for p in (root / "records/DEMO-02").glob("revision-*")) == [
        "revision-000000.json"]
    # A new revision makes the checkpoint stale; it is never applied silently.
    data = bench.record("DEMO-02")
    changed = copy.deepcopy(data["record"])
    changed["analysis"]["categories"]["behavior"]["observations"][0]["note"] = "합성 v0.1 수정"
    bench.write("save", "DEMO-02", {"record": changed, "base_hash": data["base_hash"],
                                    "actor_ref": "synthetic"})
    with pytest.raises(ValueError, match="stale"):
        AuthorSession(root, run, "DEMO-02", "synthetic-ai", PROFILE).dispatch(
            {"command": "restore"})
    # Completing the work in a fresh session saves through the common author path.
    final = AuthorSession(root, run, "DEMO-02", "synthetic-ai", PROFILE)
    record, _ = demo_profile(bench, "DEMO-02")
    record["label"]["behavior_annotation"]["basis"] = final.record["label"][
        "behavior_annotation"]["basis"]
    record["label"]["behavior_annotation"]["basis"]["change_reason"] = "합성 재관찰"
    final.dispatch({"command": "put", "record": record})
    final.dispatch({"command": "timer", "action": "RESUME"})
    final.dispatch({"command": "timer", "action": "PAUSE", "reason": "EXTERNAL_WAIT"})
    result = final.dispatch({"command": "save"})
    assert result["status"] == "CREATED" and Path(result["path"]).name == "revision-000002.json"
    final.close()
    with pytest.raises(ValueError, match="Latest record uses profile"):
        AuthorSession(root, run, "DEMO-02", "synthetic-ai")
    assert record_hash(read(Path(result["path"]))) == result["base_hash"]
