"""One-click edits/review commits and optional independent clocks on synthetic media."""

import copy
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from medication_video_annotation.demo import create_demo
from medication_video_annotation.io import file_hash
from medication_video_annotation.server import Workbench, make_server


@pytest.fixture
def bench(tmp_path):
    root = tmp_path / "합성 통합 저장"
    create_demo(root)
    return Workbench(root, root / "runs/synthetic-workbench", ["DEMO-01", "DEMO-02"])


def payload(data, status="REVIEWED", reviewer="검수자 A"):
    return {
        "record": copy.deepcopy(data["record"]),
        "base_hash": data["base_hash"],
        "actor_ref": "synthetic-editor",
        "review_status": status,
        "reviewer_ref": reviewer,
        "reviewed_ranges": [{"start_frame_index": 0, "end_frame_index": 15}],
    }


def revisions(bench, sid="DEMO-01"):
    return sorted((bench.root / "records" / sid).glob("revision-*.json"))


def test_edits_exact_review_and_reviewer_are_one_commit_without_timer(bench):
    data = bench.record("DEMO-01")
    original = revisions(bench)[0]
    digest = file_hash(original)
    value = payload(data)
    event = value["record"]["label"]["events"][0]
    event["check_answers"]["drinking_motion_visible"] = "YES"
    event["evidence"][1]["note"] = "합성 수정 근거"
    event["interval"].update(
        start_frame_index=1, start_ms=data["frames"][1]["source_ms"], boundary_status="EXACT"
    )
    # Other events keep their own chosen accuracy; review doesn't force all to EXACT.
    other = copy.deepcopy(event)
    other["event_id"] = "SYNTH-SECOND"
    other["interval"]["boundary_status"] = "UNCONFIRMED"
    value["record"]["label"]["events"].append(other)
    saved = bench.write("save", "DEMO-01", value)
    assert saved["revision_id"] == "revision-000001.json"
    assert len(revisions(bench)) == 2 and file_hash(original) == digest
    record = saved["record"]
    assert record["analysis"]["status"] == "REVIEWED"
    assert all(c["status"] == "REVIEWED" for c in record["analysis"]["categories"].values())
    assert record["label"]["coverage"]["annotation_status"] == "REVIEWED"
    event = record["label"]["events"][0]
    assert event["interval"] == value["record"]["label"]["events"][0]["interval"]
    assert event["reference_verification"] == "CONFIRMED"
    assert event["evidence"][1]["note"] == "합성 수정 근거"
    assert record["label"]["events"][1]["interval"]["boundary_status"] == "UNCONFIRMED"
    review = record["label"]["review"]
    assert review["reviewer_ref"] == "검수자 A" and review["reviewed_at_kst"]
    assert review["history"][-1]["reviewed_ranges"] == value["reviewed_ranges"]
    assert review["history"][-1]["exact_event_ids"] == ["SYNTH-E03"]
    assert bench.timer_state("DEMO-01", "HUMAN_REVIEW")["state"] == "NOT_RUN"
    assert not record["label"]["final_ground_truth"]
    assert not record["label"]["evaluation_eligible"]
    assert review["independent_review"] == "NOT_RUN"
    assert record["label"]["session_label"] == data["record"]["label"]["session_label"]
    assert not (bench.root / "exports").exists()
    assert not (bench.root / "checkpoints").exists()
    # Identical resubmission must not create a timestamp-only revision.
    assert bench.write("save", "DEMO-01", payload(saved))["status"] == "UNCHANGED"
    assert len(revisions(bench)) == 2


def test_reviewed_corrections_and_reviewer_only_changes_remain_single_commits(bench):
    data = bench.record("DEMO-01")
    value = payload(data)
    value["record"]["label"]["events"][0]["interval"]["boundary_status"] = "EXACT"
    first = bench.write("save", "DEMO-01", value)
    value = payload(first, reviewer="검수자 B")
    value["record"]["label"]["events"][0]["check_answers"]["drinking_motion_visible"] = "YES"
    second = bench.write("save", "DEMO-01", value)
    assert second["revision_id"] == "revision-000002.json"
    assert second["record"]["label"]["review"]["reviewer_ref"] == "검수자 B"
    event = second["record"]["label"]["events"][0]
    assert event["interval"]["boundary_status"] == "EXACT"
    assert event["annotation_status"] == "REVIEWED"
    assert event["reference_verification"] == "CONFIRMED"
    third = bench.write("save", "DEMO-01", payload(second, reviewer="검수자 C"))
    assert third["revision_id"] == "revision-000003.json"
    assert third["record"]["label"]["review"]["reviewer_ref"] == "검수자 C"
    draft = bench.write("save", "DEMO-01", payload(third, "DRAFT", "검수자 C"))
    assert draft["revision_id"] == "revision-000004.json"
    assert draft["record"]["label"]["review"]["reviewed_at_kst"] is None
    assert draft["record"]["label"]["events"][0]["interval"]["boundary_status"] == "APPROXIMATE"
    assert draft["record"]["label"]["events"][0]["reference_verification"] is None
    assert bench.write("save", "DEMO-01", payload(draft, "DRAFT", "검수자 C"))["status"] == "UNCHANGED"


@pytest.mark.parametrize("failure", ["reviewer", "range", "status", "stale", "evidence", "exact-draft", "protected"])
def test_failed_combined_validation_leaves_no_partial_revision(bench, failure):
    data = bench.record("DEMO-01")
    value = payload(data)
    if failure == "reviewer":
        value["reviewer_ref"] = "  "
    elif failure == "range":
        value["reviewed_ranges"][0]["end_frame_index"] = 14
    elif failure == "status":
        value["review_status"] = "INVALID"
    elif failure == "stale":
        value["base_hash"] = "0" * 64
    elif failure == "evidence":
        value["record"]["label"]["events"][0]["evidence"] = []
    elif failure == "exact-draft":
        value["review_status"] = "DRAFT"
        value["record"]["label"]["events"][0]["interval"]["boundary_status"] = "EXACT"
    else:
        value["record"]["label"]["evaluation_eligible"] = True
    before = {p: file_hash(p) for p in revisions(bench)}
    with pytest.raises(ValueError):
        bench.write("save", "DEMO-01", value)
    assert {p: file_hash(p) for p in revisions(bench)} == before
    assert bench.record("DEMO-01")["record"] == data["record"]


def test_optional_clock_finishes_without_review_and_save_does_not_close_it(bench):
    def clock(action):
        return bench.timer("DEMO-01", dict(stage="HUMAN_REVIEW", actor_ref="clock-user", action=action))

    clock("START")
    # An editor/reviewer can save independently of the timer's actor and state.
    saved = bench.write("save", "DEMO-01", payload(bench.record("DEMO-01")))
    assert bench.timer_state("DEMO-01", "HUMAN_REVIEW")["state"] == "ACTIVE"
    count = len(revisions(bench))
    assert clock("FINISH")["state"] == "COMPLETE"
    assert len(revisions(bench)) == count
    # Clock end itself must neither create a revision nor mark another clip reviewed.
    bench.timer("DEMO-02", dict(stage="HUMAN_REVIEW", actor_ref="clock-user", action="START"))
    bench.timer("DEMO-02", dict(stage="HUMAN_REVIEW", actor_ref="clock-user", action="FINISH"))
    assert bench.record("DEMO-02")["record"]["analysis"]["status"] == "DRAFT"
    assert len(revisions(bench, "DEMO-02")) == 1
    assert bench.write("save", "DEMO-01", payload(saved))["status"] == "UNCHANGED"


def test_zero_events_and_http_contract(bench):
    server = make_server(bench.root, bench.run.directory, bench.ids, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/index") as response:
            catalog = json.load(response)
        with urlopen(base + "/api/record/DEMO-02") as response:
            data = json.load(response)
        value = payload(data)
        headers = {"Content-Type": "application/json", "X-Workbench-Token": catalog["token"]}
        request = Request(base + "/api/save/DEMO-02", json.dumps(value).encode(), headers)
        with urlopen(request) as response:
            saved = json.load(response)
        assert saved["revision_id"] == "revision-000001.json"
        assert saved["record"]["label"]["events"] == []
        assert saved["record"]["label"]["coverage"]["annotation_status"] == "REVIEWED"
        with pytest.raises(HTTPError) as caught:
            urlopen(request)  # A stale retry cannot create a duplicate commit.
        assert caught.value.code == 400
        assert len(revisions(bench, "DEMO-02")) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
