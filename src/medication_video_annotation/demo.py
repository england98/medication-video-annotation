"""Isolated synthetic playable media; never reads the real dataset or past drafts."""

from pathlib import Path

import av
from PIL import Image, ImageDraw

from .io import atomic_json, file_hash, now, read, require
from .legacy_record import ANALYSIS_FIELDS
from .media import prepare_selection
from .records import instantiate, save_revision
from .timing import Run, create_run


def synthetic_record(item, mapping, with_event=True):
    record = instantiate(item["video_id"], item["source_sha256"], item["frame_map_ref"])
    count = mapping["frame_count"]
    record["analysis"].update(
        status="DRAFT", analyst="synthetic-generator", model_identifier="NOT_OBSERVED"
    )
    record["analysis"]["observation_log"] = [
        {
            "kind": "AI_VISUAL_OBSERVATION",
            "observer_ref": "SYNTHETIC_TEST_NOT_REAL_OBSERVATION",
            "recorded_at_kst": now(),
            "input_set_id": "SYNTHETIC-INPUT",
            "start_frame_index": 0,
            "end_frame_index": count - 1,
            "display_width": mapping["width"],
            "display_height": mapping["height"],
            "note": "합성 흐름 시험용 관찰 로그. 실제 사람/행동 관찰 결과가 아니다.",
        }
    ]
    for key in ANALYSIS_FIELDS:
        record["analysis"]["categories"][key] = {
            "status": "DRAFT",
            "observations": [
                {
                    "basis": "DIRECT_OBSERVATION",
                    "note": "합성 도형 영상의 기능 시험용 설명입니다.",
                    "frame_indices": [0, count - 1],
                }
            ],
        }
    span = {
        "start_frame_index": 0,
        "end_frame_index": count - 1,
        "start_ms": mapping["frames"][0]["source_ms"],
        "end_ms": mapping["frames"][-1]["source_ms"],
    }
    label = record["label"]
    label.update(
        label_version="v0.1-draft",
        coverage={
            "annotation_status": "DRAFT",
            "reviewed_ranges": [span],
            "event_types": ["E01", "E02", "E03"],
            "exhaustive": True,
        },
    )
    if with_event:
        label["events"] = [
            {
                "event_id": "SYNTH-E03",
                "event_type": "E03",
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
                    "container_reaches_mouth": "YES",
                    "drinking_motion_visible": "UNKNOWN",
                },
                "evidence": [
                    {
                        "check": key,
                        "frame_index": 1,
                        "source_ms": mapping["frames"][1]["source_ms"],
                        "role": "OBSERVATION",
                        "note": "합성 기능 검사 근거. 실제 음수 판단 아님.",
                    }
                    for key in ("container_reaches_mouth", "drinking_motion_visible")
                ],
                "limitations": [
                    {
                        "check": "drinking_motion_visible",
                        "reason": "합성 시험용 불확실성.",
                        "frame_indices": [1],
                    }
                ],
                "links": [],
                "reference_verification": None,
                "verification_rule_version": "architecture-2-4-current",
            }
        ]
    else:
        label["observations"] = [
            {**span, "kind": "NO_TARGET_EVENT_OBSERVED", "reason": "합성 도형의 0 이벤트 시험."}
        ]
    label["session_label"].update(
        annotation_status="NOT_APPLICABLE", reason="합성 영상; 실제 세션 아님."
    )
    return record


def create_demo(root):
    root = Path(root).resolve()
    marker = root / "synthetic-demo.json"
    if root.exists():
        require(
            marker.exists() and read(marker).get("synthetic_only") is True,
            "Refuse to populate an existing non-demo directory",
        )
        return read(marker)  # Never reset existing demo reviews or revisions.
    root.mkdir(parents=True)
    items = []
    for n in (1, 2):
        sid, filename = f"DEMO-{n:02d}", f"synthetic_demo_{n:02d}.mp4"
        base = "data/SYNTHETIC/기능시험"
        source = root / base / "videos" / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(source), "w") as output:
            stream = output.add_stream("libx264", rate=25)
            stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
            for i in range(16):
                image = Image.new("RGB", (640, 360), (18, 40 + n * 30, 65 + i * 3))
                draw = ImageDraw.Draw(image)
                draw.rectangle((50 + i * 10, 100, 170 + i * 10, 240), fill=(60, 195, 185))
                draw.text(
                    (30, 30), f"SYNTHETIC TEST {n} / frame {i:02d} - no real person", fill="white"
                )
                frame = av.VideoFrame.from_image(image)
                frame.pts = i + 35
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        items.append(
            {
                "format": "onc110-dataset-index-entry-v0.1",
                "video_id": sid,
                "source_sha256": file_hash(source),
                "source_path": source.relative_to(root).as_posix(),
                "original_filename": filename,
                "classification": {"tier": "SYNTHETIC", "behavior": "기능시험"},
                "frame_map_ref": f"{base}/frames/{source.stem}/frame-map.json",
                "record_dir": f"records/{sid}",
                "source_lineage": {"origin": "SYNTHETIC", "aliases": [], "original_path": None},
            }
        )
    run_path = create_run(
        root / "runs", "synthetic-workbench", "TRIAL", video_ids=[v["video_id"] for v in items]
    )
    run = Run(run_path)
    run.start("synthetic-selection", "SELECTION")
    run.transition(
        "synthetic-selection",
        "FINISH",
        result="COMPLETE",
        reason="Generated synthetic fixtures only",
    )
    atomic_json(root / "selection.json", items)
    prepare_selection(root, items, run)
    for n, item in enumerate(items):
        mapping = read(root / item["frame_map_ref"])
        record = synthetic_record(item, mapping, with_event=n == 0)
        save_revision(root, item, mapping, record, base_hash=None, actor="synthetic-generator")
    value = {
        "synthetic_only": True,
        "ids": [v["video_id"] for v in items],
        "run": run_path.relative_to(root).as_posix(),
        "created_at_kst": now(),
    }
    atomic_json(marker, value, immutable=True)
    return value
