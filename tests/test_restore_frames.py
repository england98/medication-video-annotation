"""Frame PNGs are derived data: re-created from the source video only when hashes match."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from medication_video_annotation.demo import create_demo
from medication_video_annotation.io import file_hash, read
from medication_video_annotation.media import restore_frames, verify_prepared
from medication_video_annotation.records import load_index


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    create_demo(root)
    items = {v["video_id"]: v for v in load_index(root)}
    return root, items


def frames_of(root, item):
    return read(root / item["frame_map_ref"])["frames"]


def test_missing_frames_are_recreated_byte_identically(demo):
    root, items = demo
    item = items["DEMO-01"]
    frames = frames_of(root, item)
    removed = [frames[0], frames[7], frames[-1]]
    for frame in removed:
        (root / frame["image_path"]).unlink()
    map_before = file_hash(root / item["frame_map_ref"])
    result = restore_frames(root, item)
    assert result == {"video_id": "DEMO-01", "frames": len(frames), "restored": 3}
    for frame in removed:
        assert file_hash(root / frame["image_path"]) == frame["image_sha256"]
    assert file_hash(root / item["frame_map_ref"]) == map_before
    verify_prepared(root, item)
    assert restore_frames(root, item)["restored"] == 0
    assert not list((root / item["frame_map_ref"]).parent.glob("*.pending.png"))


def test_existing_changed_frame_is_not_overwritten(demo):
    root, items = demo
    frame = frames_of(root, items["DEMO-01"])[3]
    path = root / frame["image_path"]
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Existing frame changed"):
        restore_frames(root, items["DEMO-01"])
    assert path.read_bytes() == b"changed"


def test_changed_source_is_refused(demo):
    root, items = demo
    item = items["DEMO-02"]
    (root / frames_of(root, item)[0]["image_path"]).unlink()
    with (root / item["source_path"]).open("ab") as source:
        source.write(b"tail")
    with pytest.raises(ValueError, match="Source bytes changed"):
        restore_frames(root, item)


def test_hash_mismatch_leaves_no_frame(demo):
    root, items = demo
    item = items["DEMO-01"]
    map_path = root / item["frame_map_ref"]
    mapping = read(map_path)
    mapping["frames"][2]["image_sha256"] = "0" * 64
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    target = root / mapping["frames"][2]["image_path"]
    target.unlink()
    with pytest.raises(ValueError, match="differs from its recorded hash"):
        restore_frames(root, item)
    assert not target.exists() and not target.with_suffix(".pending.png").exists()


def test_cli_restores_all_videos(demo):
    root, items = demo
    for item in items.values():
        for frame in frames_of(root, item)[:2]:
            (root / frame["image_path"]).unlink()
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "medication_video_annotation", "--data-root", str(root),
         "restore-frames", "--all"],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "PASS" and value["videos"] == 2 and value["restored_frames"] == 4
