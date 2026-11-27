"""Byte-preserving legacy import and subsequent common save regression."""

import copy
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest
from build_synthetic import SOURCE_BYTES, fixture
from PIL import Image

from medication_video_annotation.io import atomic_json, file_hash, read
from medication_video_annotation.migration import import_one, inspect_pilot, verify_import
from medication_video_annotation.records import load_index, record_hash, save_revision
from medication_video_annotation.server import make_server
from medication_video_annotation.timing import Run, create_run
from medication_video_annotation.workflow import latest


@pytest.fixture
def pilot(tmp_path):
    _, sample, record = fixture()
    sid = "ONC110-SYNTH"
    sample.update(video_id=sid, sample_id=sid)
    record["video_id"] = record["label"]["video_id"] = record["label"]["source_ref"] = sid
    record["label"]["frame_map_ref"] = f"frame-map.private.json#samples/{sid}"
    record["extension"] = {"preserve": "한글 및 확장 필드"}
    source = tmp_path / "data_curated/분류/계층/행동/원본.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(SOURCE_BYTES)
    sample["source_path"] = str(source)
    for frame in sample["frames"]:
        frame.pop("image_path")
    for key in ("format", "generation"):
        sample.pop(key)
    src = tmp_path / "pipeline"
    input_id = "SYNTHETIC-INPUT"
    for log in record["analysis"]["observation_log"]:
        log["input_set_id"] = input_id
    inputs = src / "frames" / sid / input_id
    inputs.mkdir(parents=True)
    images = []
    for frame in sample["frames"]:
        path = inputs / f"frame-{frame['frame_index']:06d}.png"
        Image.new("RGB", (sample["width"], sample["height"])).save(path)
        images.append(
            {
                **frame,
                "image_path": str(path),
                "sha256": file_hash(path),
                "width": sample["width"],
                "height": sample["height"],
            }
        )
    atomic_json(
        inputs / "input-manifest.json",
        {"video_id": sid, "source_sha256": sample["source_sha256"], "frames": images},
    )
    atomic_json(inputs / "source-map.private.json", sample)
    atomic_json(src / "frame-map.private.json", {"samples": {sid: sample}})
    atomic_json(
        src / "run-config.json", {"input": {"sampling": "EVERY_SOURCE_FRAME", "resize": False}}
    )
    atomic_json(
        src / "confirmed-selection.private.json",
        {
            "selected": [
                {
                    "candidate_id": "SYNTH",
                    "dest_path": str(source),
                    "original_path": "synthetic origin",
                }
            ]
        },
    )
    atomic_json(src / f"records/{sid}/revision-000002.json", record)
    root = tmp_path / "destination"
    root.mkdir()
    run = Run(create_run(root / "runs", "migration", "MIGRATION", video_ids=[sid]))
    return root, src, sid, run


def test_import_read_noop_update_and_http(pilot):
    root, src, sid, run = pilot
    plan = inspect_pilot(root, src, {sid: "revision-000002.json"})[0]
    before = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}
    result = import_one(root, plan, run)
    assert result["status"] == "PASS"
    assert all(p.read_bytes() == data for p, data in before.items())
    item = load_index(root)[0]
    record, path = latest(root, item)
    original = path.read_bytes()
    saved = save_revision(
        root, item, plan["mapping"], record, base_hash=record_hash(record), actor="synthetic"
    )
    assert saved["status"] == "UNCHANGED"
    assert len(list(path.parent.glob("revision-*.json"))) == 1
    server = make_server(root, run.directory, [sid], 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for suffix, expected in [
            (f"/api/video/{sid}", SOURCE_BYTES),
            (f"/api/frame/{sid}/0", Path(plan["files"][1]["source"]).read_bytes()),
        ]:
            with urlopen(base + suffix) as response:
                assert response.read() == expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert not (root / "cache").exists() and not (root / "exports").exists()
    changed = copy.deepcopy(record)
    changed["analysis"]["categories"]["behavior"]["observations"][0]["note"] = "합성 수정"
    saved = save_revision(
        root, item, plan["mapping"], changed, base_hash=record_hash(record), actor="synthetic"
    )
    assert Path(saved["path"]).name == "revision-000003.json"
    assert path.read_bytes() == original
    assert saved["record"]["extension"] == record["extension"]
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="baseline bytes"):
        latest(root, item)


@pytest.mark.parametrize("change", ["latest", "source", "png", "map"])
def test_preflight_changes_rejected_before_copy(pilot, change):
    root, src, sid, run = pilot
    plan = inspect_pilot(root, src, {sid: "revision-000002.json"})[0]
    if change == "latest":
        atomic_json(src / f"records/{sid}/revision-000003.json", {})
    elif change == "map":
        atomic_json(src / "frame-map.private.json", {})
    else:
        Path(plan["files"][0 if change == "source" else 1]["source"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        import_one(root, plan, run)
    assert not (root / "data").exists() and not (root / "records").exists()


def test_import_tampering_and_gaps_fail_closed(pilot):
    root, src, sid, run = pilot
    plan = inspect_pilot(root, src, {sid: "revision-000002.json"})[0]
    import_one(root, plan, run)
    item = plan["item"]
    atomic_json(root / item["record_dir"] / "revision-000004.json", {})
    with pytest.raises(ValueError, match="gaps"):
        latest(root, item)
    mapping = read(root / item["frame_map_ref"])
    mapping["frames"][0]["source_ms"] += 1
    atomic_json(root / item["frame_map_ref"], mapping)
    with pytest.raises(ValueError, match="map bytes"):
        verify_import(root, plan)


def test_migration_completion_does_not_claim_observation(pilot):
    _, _, sid, run = pilot
    for task, stage, video in [
        ("selection", "SELECTION", None),
        ("copy", "PREPARATION", sid),
        ("verify", "VALIDATE_SAVE", sid),
    ]:
        run.start(task, stage, video_id=video)
        run.transition(task, "FINISH", result="COMPLETE")
    run.finish_run()
    summary = run.summary()
    assert summary["status"] == "COMPLETE" and summary["completion_scope"] == "MIGRATION"
    assert summary["human_review_status"] == "NOT_RUN"
    assert all(
        row["status"] == "NOT_RUN"
        for row in summary["trial_scopes"]
        if row["stage"] in {"OBSERVATION_DRAFT", "EXPORT", "HUMAN_REVIEW"}
    )
