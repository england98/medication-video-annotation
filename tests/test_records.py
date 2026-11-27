import ast
import copy
import csv
import io
import json
from pathlib import Path

import pytest
from build_synthetic import SOURCE_BYTES, fixture

from medication_video_annotation.export_format import bundle_bytes
from medication_video_annotation.io import atomic_json, digest_bytes, encoded, read
from medication_video_annotation.records import (
    TEMPLATE,
    instantiate,
    load_index,
    save_revision,
    validate_link,
)


def materialize(root):
    item, mapping, record = fixture()
    source = root / item["source_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(SOURCE_BYTES)
    for frame in mapping["frames"]:
        image = root / frame["image_path"]
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"SYNTHETIC NOT A REAL IMAGE")
    atomic_json(root / item["frame_map_ref"], mapping)
    record["revision_history"] = []
    return item, mapping, record


def test_initial_noop_change_conflict_and_extensions(tmp_path):
    item, mapping, record = materialize(tmp_path / "한글 경로")
    root = tmp_path / "한글 경로"
    record["extension"] = {"preserved": True}
    record["label"]["extension"] = {"unknown_value": None}
    saved = save_revision(root, item, mapping, record, base_hash=None, actor="tester")
    assert Path(saved["path"]).name == "revision-000000.json"
    original = Path(saved["path"]).read_bytes()
    again = save_revision(
        root, item, mapping, saved["record"], base_hash=saved["base_hash"], actor="tester"
    )
    assert again["status"] == "UNCHANGED"
    changed = copy.deepcopy(saved["record"])
    changed["analysis"]["categories"]["behavior"]["observations"][0]["note"] = "수정된 합성 설명"
    second = save_revision(
        root, item, mapping, changed, base_hash=saved["base_hash"], actor="tester"
    )
    assert Path(second["path"]).name == "revision-000001.json"
    assert second["record"]["extension"] == record["extension"]
    assert second["record"]["label"]["extension"] == record["label"]["extension"]
    assert Path(saved["path"]).read_bytes() == original
    assert second["record"]["revision_history"][-1]["previous_sha256"] == saved["base_hash"]
    with pytest.raises(ValueError, match="changed"):
        save_revision(root, item, mapping, changed, base_hash=saved["base_hash"], actor="tester")
    assert not (root / "exports").exists()


def test_blank_rejected_without_record_directory(tmp_path):
    item, mapping, _ = materialize(tmp_path)
    blank = instantiate(item["video_id"], item["source_sha256"], item["frame_map_ref"])
    with pytest.raises(ValueError, match="Empty analysis"):
        save_revision(tmp_path, item, mapping, blank, base_hash=None, actor="tester")
    assert not (tmp_path / "records").exists()
    assert set(blank) == set(read(TEMPLATE))


@pytest.mark.parametrize("change", ["source", "pts", "path", "traversal", "nan", "missing-frame"])
def test_bad_link_rejected(tmp_path, change):
    item, mapping, record = materialize(tmp_path)
    if change == "source":
        (tmp_path / item["source_path"]).write_bytes(b"changed")
    elif change == "pts":
        mapping["frames"][1]["player_elapsed_ms"] = 999
    elif change == "path":
        item["original_filename"] = "renamed.mp4"
    elif change == "traversal":
        mapping["frames"][0]["image_path"] = "../outside.png"
    elif change == "nan":
        mapping["frames"][0]["source_ms"] = float("nan")
    else:
        (tmp_path / mapping["frames"][0]["image_path"]).unlink()
    with pytest.raises(ValueError):
        save_revision(tmp_path, item, mapping, record, base_hash=None, actor="tester")
    assert not (tmp_path / "records").exists()


def test_zero_events_and_timing_only_no_revision(tmp_path):
    item, mapping, record = materialize(tmp_path)
    record["label"]["events"] = []
    record["label"]["observations"] = [
        {
            **record["label"]["coverage"]["reviewed_ranges"][0],
            "kind": "NO_TARGET_EVENT_OBSERVED",
            "reason": "합성 부재 관찰",
        }
    ]
    saved = save_revision(tmp_path, item, mapping, record, base_hash=None, actor="tester")
    timing = copy.deepcopy(saved["record"])
    timing["measurements"].append(
        {
            "stage": "HUMAN_REVIEW",
            "seconds": 3,
            "actor_ref": "tester",
            "recorded_at_kst": "2026-10-17T12:00:00+09:00",
        }
    )
    assert (
        save_revision(
            tmp_path, item, mapping, timing, base_hash=saved["base_hash"], actor="tester"
        )["status"]
        == "UNCHANGED"
    )
    assert read(saved["path"])["measurements"] == []
    files = bundle_bytes(
        saved["record"],
        revision_ref="records/SYNTH-0001/revision-000000.json",
        revision_file_sha256=digest_bytes(encoded(saved["record"])),
        exported_at_kst="2026-10-17T12:00:00+09:00",
        purpose="SYNTHETIC",
    )
    assert len(list(csv.reader(io.StringIO(files["events.csv"].decode("utf-8-sig"))))) == 1


def test_export_set_is_consistent():
    folder = Path("templates/exports/synthetic")
    assert {p.name for p in folder.iterdir()} == {
        "record.json",
        "analysis.json",
        "label.json",
        "events.csv",
        "export-manifest.json",
    }
    record, manifest = read(folder / "record.json"), read(folder / "export-manifest.json")
    assert record["label"]["events"][0]["check_answers"]["object_is_medication"] == "UNKNOWN"
    assert record["label"]["events"][0]["subject_key"] is None
    assert read(folder / "analysis.json") == record["analysis"]
    assert read(folder / "label.json") == record["label"]
    for name, info in manifest["files"].items():
        data = (folder / name).read_bytes()
        assert len(data) == info["bytes"] and digest_bytes(data) == info["sha256"]
    assert "export-manifest.json" not in manifest["files"]
    assert digest_bytes((folder / "record.json").read_bytes()) == manifest["revision_file_sha256"]


def test_index_duplicates_and_nonzero_pts(tmp_path):
    item, mapping, _ = materialize(tmp_path)
    assert validate_link(tmp_path, item, mapping)["frames"][0]["source_ms"] == 5000
    directory = tmp_path / "manifests"
    directory.mkdir()
    index = directory / "dataset-index.jsonl"
    index.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
    assert len(load_index(tmp_path)) == 1
    index.write_text((json.dumps(item) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_index(tmp_path)


def test_reused_validator_and_csv_preserve_upstream_ast():
    provenance = read("docs/reuse-provenance.json")
    source = Path("src/medication_video_annotation/legacy_record.py").read_text(encoding="utf-8")
    functions = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    for name, expected in provenance["function_ast_sha256"].items():
        assert (
            digest_bytes(ast.dump(functions[name], include_attributes=False).encode()) == expected
        )


def test_immutable_file_never_overwrites(tmp_path):
    p = tmp_path / "revision-000000.json"
    atomic_json(p, {"before": True}, immutable=True)
    with pytest.raises(FileExistsError):
        atomic_json(p, {"after": True}, immutable=True)
    assert read(p) == {"before": True}


def test_nested_extension_loss_and_fake_review_rejected(tmp_path):
    item, mapping, record = materialize(tmp_path)
    record["label"]["extension"] = {"keep": True}
    saved = save_revision(tmp_path, item, mapping, record, base_hash=None, actor="tester")
    missing = copy.deepcopy(saved["record"])
    del missing["label"]["extension"]
    with pytest.raises(ValueError, match="Preserve existing"):
        save_revision(
            tmp_path, item, mapping, missing, base_hash=saved["base_hash"], actor="tester"
        )
    invalid = copy.deepcopy(saved["record"])
    invalid["label"]["events"][0]["interval"]["boundary_status"] = "EXACT"
    with pytest.raises(ValueError, match="human review"):
        save_revision(
            tmp_path, item, mapping, invalid, base_hash=saved["base_hash"], actor="tester"
        )
