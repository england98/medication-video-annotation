"""Native PNG/PTS preparation and packet-preserving playback, adapted from ONC-110."""

import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image

from .io import atomic_json, encoded, file_hash, identifier, read, relative, require, writer_lock
from .operations import latest_task, operation
from .records import validate_link
from .timing import TERMINAL

GENERATION = {
    "sampling": "EVERY_SOURCE_FRAME",
    "resize": False,
    "decoder_version": f"PyAV-{av.__version__}",
    "tool_version": "native-frames-v0.1",
}


def inspect_source(path):
    # Retained from onc110_cvat.inspect_source; no CVAT transport dependency.
    frames, dimensions = [], None
    with av.open(str(path)) as container:
        require(len(container.streams.video) == 1, "Expected one video stream")
        for index, frame in enumerate(container.decode(video=0)):
            require(frame.pts is not None and frame.time_base is not None, "Missing PTS")
            source_ms = float(frame.pts * frame.time_base * 1000)
            require(not frames or source_ms > frames[-1]["source_ms"], "Non-increasing source PTS")
            require(
                dimensions is None or (frame.width, frame.height) == dimensions,
                "Changing source dimensions",
            )
            dimensions = frame.width, frame.height
            frames.append(
                {
                    "frame_index": index,
                    "pts": frame.pts,
                    "time_base": str(frame.time_base),
                    "source_ms": source_ms,
                }
            )
    require(frames, "Empty source")
    origin = frames[0]["source_ms"]
    for frame in frames:
        frame["player_elapsed_ms"] = frame["source_ms"] - origin
    return {
        "width": dimensions[0],
        "height": dimensions[1],
        "frame_count": len(frames),
        "first_source_ms": origin,
        "frames": frames,
    }


def verify_prepared(root, item):
    mapping = read(relative(root, item["frame_map_ref"]))
    validate_link(root, item, mapping, verify_files=True)
    require(
        mapping["generation"] == item.get("imported_revision", {}).get("generation", GENERATION),
        "Extraction version changed; explicit migration required",
    )
    for frame in mapping["frames"]:
        image_path = relative(root, frame["image_path"])
        require(file_hash(image_path) == frame["image_sha256"], "Prepared frame hash changed")
        with Image.open(image_path) as image:
            require(image.size == (mapping["width"], mapping["height"]), "PNG dimensions changed")
            image.verify()
    return mapping


def prepare_one(root, item, run):
    root = Path(root).resolve()
    sid = identifier(item["video_id"])
    source = relative(root, item["source_path"])
    require(file_hash(source) == item["source_sha256"], "Source bytes changed")
    directory = relative(root, item["frame_map_ref"]).parent
    if (directory / "frame-map.json").exists():
        mapping = verify_prepared(root, item)
        old = latest_task(run, f"frames-{sid}")
        if old and old["state"] not in TERMINAL:
            with operation(run, f"frames-{sid}", "PREPARATION", sid):
                pass  # Valid committed artifact: close interrupted task without extracting again.
        return mapping, "REUSED"
    with operation(run, f"frames-{sid}", "PREPARATION", sid), writer_lock(directory):
        meta = inspect_source(source)
        mapping = {
            "format": "onc110-frame-map-v0.1",
            "video_id": sid,
            "source_path": item["source_path"],
            "source_sha256": item["source_sha256"],
            "generation": GENERATION,
            **meta,
        }
        for frame in mapping["frames"]:
            frame["image_path"] = (
                (directory / f"frame_{frame['frame_index']:06d}.png").relative_to(root).as_posix()
            )
        validate_link(root, item, mapping)
        state_path = directory / "preparation-state.json"
        state = (
            read(state_path)
            if state_path.exists()
            else {"source_sha256": item["source_sha256"], "generation": GENERATION, "completed": {}}
        )
        require(
            state["source_sha256"] == item["source_sha256"] and state["generation"] == GENERATION,
            "Preparation checkpoint does not match source/version",
        )
        atomic_json(state_path, state)
        count = 0
        with av.open(str(source)) as container:
            for i, frame in enumerate(container.decode(video=0)):
                require(
                    i < len(mapping["frames"]) and frame.pts is not None, "Source decode changed"
                )
                info = mapping["frames"][i]
                require(
                    abs(float(frame.pts * frame.time_base * 1000) - info["source_ms"]) < 1e-5,
                    "Decoded PTS differs from inspection",
                )
                require(
                    (frame.width, frame.height) == (meta["width"], meta["height"]), "Size changed"
                )
                target = relative(root, info["image_path"])
                if str(i) in state["completed"]:
                    require(
                        target.exists() and file_hash(target) == state["completed"][str(i)],
                        "Completed frame missing/changed; do not silently overwrite",
                    )
                else:
                    image = frame.to_image().convert("RGB")
                    temporary = target.with_suffix(".pending.png")
                    image.save(temporary, compress_level=1)
                    if target.exists():
                        require(
                            file_hash(target) == file_hash(temporary), "Untracked frame conflicts"
                        )
                        temporary.unlink()
                    else:
                        os.rename(temporary, target)
                    state["completed"][str(i)] = file_hash(target)
                    atomic_json(state_path, state)
                info["image_sha256"] = state["completed"][str(i)]
                count += 1
        require(count == meta["frame_count"], "Decoded frame count changed")
        chunks = [
            {
                "chunk_id": f"{sid}-C{n:04d}",
                "start_frame_index": start,
                "end_frame_index": min(start + 11, count - 1),
                "observation_status": "NOT_VIEWED",
            }
            for n, start in enumerate(range(0, count, 10))
        ]
        # Avoid a redundant final chunk wholly contained in the preceding overlap.
        if len(chunks) > 1 and chunks[-1]["end_frame_index"] == chunks[-2]["end_frame_index"]:
            chunks.pop()
        chunk_value = {"chunk_frames": 12, "overlap_frames": 2, "chunks": chunks}
        chunk_path = directory / "chunks.json"
        if chunk_path.exists():
            require(read(chunk_path) == chunk_value, "Existing chunks changed")
        else:
            atomic_json(chunk_path, chunk_value, immutable=True)
        atomic_json(directory / "frame-map.json", mapping, immutable=True)
        state.update(status="COMPLETE")
        atomic_json(state_path, state)
    return verify_prepared(root, item), "PREPARED"


def playback(root, item, mapping, run):
    if item.get("imported_revision"):
        source = relative(root, item["source_path"])
        require(file_hash(source) == item["source_sha256"], "Playback source changed")
        return source  # Serve preserved media; imports never create a playback copy.
    directory = relative(root, f"cache/{identifier(item['video_id'])}")
    target, manifest = directory / "playback.mp4", directory / "playback.json"
    if manifest.exists() and target.exists():
        saved = read(manifest)
        require(saved["source_sha256"] == item["source_sha256"], "Playback source mismatch")
        require(saved["sha256"] == file_hash(target), "Playback cache changed")
        task_id = f"playback-{item['video_id']}"
        old = latest_task(run, task_id)
        if old and old["state"] not in TERMINAL:
            with operation(run, task_id, "PREPARATION", item["video_id"]):
                pass
        return target
    with (
        operation(run, f"playback-{item['video_id']}", "PREPARATION", item["video_id"]),
        writer_lock(directory),
    ):
        source_path = relative(root, item["source_path"])
        require(file_hash(source_path) == item["source_sha256"], "Playback source changed")
        temporary = directory / "playback.pending.mp4"
        original_digest, original_count = hashlib.sha256(), 0
        with av.open(str(source_path)) as source, av.open(str(temporary), "w") as destination:
            stream = source.streams.video[0]
            copied = destination.add_stream_from_template(stream)
            offset = Fraction(str(mapping["first_source_ms"])) / 1000 / stream.time_base
            require(offset.denominator == 1, "PTS origin cannot be mapped exactly")
            for packet in source.demux(stream):
                if packet.dts is None:
                    continue
                require(packet.pts is not None, "Packet PTS missing")
                original_digest.update(bytes(packet))
                original_count += 1
                packet.pts -= int(offset)
                packet.dts -= int(offset)
                packet.stream = copied
                destination.mux(packet)
        with av.open(str(temporary)) as decoded:
            playback_digest, playback_count = hashlib.sha256(), 0
            for packet in decoded.demux(video=0):
                if packet.dts is not None:
                    playback_digest.update(bytes(packet))
                    playback_count += 1
            require(
                original_digest.digest() == playback_digest.digest()
                and original_count == playback_count,
                "Playback packet bytes changed",
            )
        meta = inspect_source(temporary)
        require(
            meta["frame_count"] == mapping["frame_count"]
            and (meta["width"], meta["height"]) == (mapping["width"], mapping["height"]),
            "Playback size/count mismatch",
        )
        require(
            all(
                abs(a["source_ms"] - b["player_elapsed_ms"]) < 1e-5
                for a, b in zip(meta["frames"], mapping["frames"])
            ),
            "Playback PTS mismatch",
        )
        os.replace(temporary, target)  # Derived cache only; source media is never overwritten.
        atomic_json(
            manifest,
            {
                "source_sha256": item["source_sha256"],
                "sha256": file_hash(target),
                "video_packets_unchanged": True,
                "audio_included": False,
                "first_source_ms": mapping["first_source_ms"],
            },
        )
    return target


def prepare_selection(root, selected, run):
    require(
        selected and len({v["video_id"] for v in selected}) == len(selected),
        "Empty/duplicate selection",
    )
    require(
        len({v["source_sha256"] for v in selected}) == len(selected), "Duplicate source content"
    )
    paths = [
        v[k].casefold() for v in selected for k in ("source_path", "frame_map_ref", "record_dir")
    ]
    require(len(set(paths)) == len(paths), "Selection path collision")
    run._refresh_targets()
    require(
        set(v["video_id"] for v in selected) <= set(run.meta["video_ids"]),
        "Selection outside run targets",
    )
    manifest_dir = Path(root) / "manifests"
    if (manifest_dir / "dataset-index.jsonl").exists():
        existing_items = [
            json.loads(line)
            for line in (manifest_dir / "dataset-index.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        require(
            len({v["video_id"] for v in existing_items}) == len(existing_items),
            "Duplicate index video IDs",
        )
        for item in selected:
            for existing in existing_items:
                if existing["video_id"] == item["video_id"]:
                    require(existing == item, "Existing index identity changed")
                else:
                    require(
                        existing["source_sha256"] != item["source_sha256"]
                        and not any(
                            existing[k].casefold() == item[k].casefold()
                            for k in ("source_path", "frame_map_ref", "record_dir")
                        ),
                        "Index collision",
                    )
    results = []
    for item in selected:
        mapping, status = prepare_one(root, item, run)
        playback(root, item, mapping, run)
        with writer_lock(manifest_dir):
            index = manifest_dir / "dataset-index.jsonl"
            old = (
                [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
                if index.exists()
                else []
            )
            existing = next((v for v in old if v["video_id"] == item["video_id"]), None)
            if existing:
                require(existing == item, "Existing index identity changed")
            else:
                require(
                    not any(
                        v["source_sha256"] == item["source_sha256"]
                        or any(
                            v[k].casefold() == item[k].casefold()
                            for k in ("source_path", "frame_map_ref", "record_dir")
                        )
                        for v in old
                    ),
                    "Index collision",
                )
                temporary = manifest_dir / "dataset-index.pending.jsonl"
                temporary.write_bytes(
                    b"".join(encoded(v).replace(b"\n", b"") + b"\n" for v in old + [item])
                )
                os.replace(temporary, index)
        results.append(
            {"video_id": item["video_id"], "status": status, "frames": mapping["frame_count"]}
        )
    return results
