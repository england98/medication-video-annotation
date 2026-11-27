"""Explicit pilot imports: preserve payload bytes and bind legacy references locally.

No decoding, extraction, authoring, revision creation, or exports occur here.
Source workspaces are read-only. Only caller-selected IDs are inspected/imported.
"""

import json
import os
import shutil
from pathlib import Path

from .io import atomic_json, encoded, file_hash, now, read, relative, require, writer_lock
from .media import verify_prepared
from .operations import operation
from .records import load_index, record_frame_map_ref, revision_paths, validate_draft


def inspect_pilot(root, source_root, selections):
    """Return an in-memory plan; selections maps stable IDs to exact revision names."""
    root, source_root = Path(root).resolve(), Path(source_root).resolve(strict=True)
    existing = load_index(root) if (root / "manifests/dataset-index.jsonl").exists() else []
    samples = read(source_root / "frame-map.private.json")["samples"]
    configuration = read(source_root / "run-config.json")["input"]
    require(
        configuration["sampling"] == "EVERY_SOURCE_FRAME" and configuration["resize"] is False,
        "Source preparation is not native/full-frame",
    )
    selected = read(source_root / "confirmed-selection.private.json")["selected"]
    plans = []
    for sid, revision_name in selections.items():
        sample = samples[sid]
        source = Path(sample["source_path"]).resolve(strict=True)
        require("data_curated" in source.parts, "Original must come from recorded data_curated")
        lineage_matches = [v for v in selected if Path(v["dest_path"]).resolve() == source]
        require(len(lineage_matches) == 1, "Ambiguous/missing source lineage")
        lineage = lineage_matches[0]
        require(Path(lineage["dest_path"]).resolve() == source, "Source lineage disagrees")
        revisions = sorted((source_root / "records" / sid).glob("revision-*.json"))
        require(
            revisions and revisions[-1].name == revision_name, "Requested revision is not latest"
        )
        revision = revisions[-1]
        record = read(revision)
        alias = f"frame-map.private.json#samples/{sid}"
        validate_draft(record, sample, alias)
        require(
            record["analysis"]["status"] == "DRAFT"
            and record["label"]["coverage"]["annotation_status"] == "DRAFT",
            "Expected a completed draft",
        )
        input_ids = {v["input_set_id"] for v in record["analysis"]["observation_log"]}
        require(len(input_ids) == 1, "Ambiguous observed input set")
        inputs = source_root / "frames" / sid / next(iter(input_ids))
        require(inputs.resolve().is_relative_to(source_root), "Input path escapes source")
        manifest = read(inputs / "input-manifest.json")
        require(
            manifest["video_id"] == sid and manifest["source_sha256"] == sample["source_sha256"],
            "Input identity mismatch",
        )
        require(read(inputs / "source-map.private.json") == sample, "Source maps disagree")
        require(len(manifest["frames"]) == sample["frame_count"], "Input frame count differs")
        tier, behavior = source.parent.parent.name, source.parent.name
        base = f"data/{tier}/{behavior}"
        folder = f"{base}/frames/{source.stem}"
        item = {
            "format": "onc110-dataset-index-entry-v0.1",
            "video_id": sid,
            "source_sha256": sample["source_sha256"],
            "original_filename": source.name,
            "classification": {"tier": tier, "behavior": behavior},
            "source_path": f"{base}/videos/{source.name}",
            "frame_map_ref": f"{folder}/frame-map.json",
            "record_dir": f"records/{sid}",
            "source_lineage": {
                "curated_path": str(source),
                "original_path": lineage["original_path"],
                "imported_from": str(source_root),
                "source_retained": True,
            },
        }
        generation = {
            "sampling": "EVERY_SOURCE_FRAME",
            "resize": False,
            "decoder_version": "NOT_RECORDED_IN_SOURCE_MANIFEST",
            "tool_version": "onc110-frame-input-v0.1-preserved-import",
        }
        mapping = {
            **sample,
            "format": "onc110-frame-map-v0.1",
            "source_path": item["source_path"],
            "generation": generation,
            "frames": [],
        }
        files = [
            {
                "source": str(source),
                "destination": item["source_path"],
                "sha256": sample["source_sha256"],
                "kind": "VIDEO",
            }
        ]
        for frame, image in zip(sample["frames"], manifest["frames"]):
            index = frame["frame_index"]
            path = inputs / f"frame-{index:06d}.png"
            require(
                Path(image["image_path"]).resolve() == path.resolve()
                and image["frame_index"] == index
                and image["source_ms"] == frame["source_ms"]
                and (image["width"], image["height"]) == (sample["width"], sample["height"]),
                "Input PNG/PTS/dimensions mismatch",
            )
            destination = f"{folder}/{path.name}"
            mapping["frames"].append(
                {**frame, "image_path": destination, "image_sha256": image["sha256"]}
            )
            files.append(
                {
                    "source": str(path),
                    "destination": destination,
                    "sha256": image["sha256"],
                    "kind": "PNG",
                }
            )
        require(
            {v.name for v in inputs.glob("*.png")}
            == {Path(v["source"]).name for v in files if v["kind"] == "PNG"},
            "Unexpected PNG set",
        )
        files.append(
            {
                "source": str(revision),
                "destination": f"records/{sid}/{revision_name}",
                "sha256": file_hash(revision),
                "kind": "REVISION",
            }
        )
        from .io import digest_bytes

        item["imported_revision"] = {
            "format": "onc110-imported-revision-v0.1",
            "filename": revision_name,
            "sha256": files[-1]["sha256"],
            "frame_map_ref": alias,
            "frame_map_sha256": digest_bytes(encoded(mapping)),
            "generation": generation,
        }
        for other in existing + [p["item"] for p in plans]:
            require(
                other["video_id"] != sid
                and other["source_sha256"] != item["source_sha256"]
                and not any(
                    other[k].casefold() == item[k].casefold()
                    for k in ("source_path", "frame_map_ref", "record_dir")
                ),
                "Existing ID/content/path collision",
            )
        for payload in files:
            require(
                not relative(root, payload["destination"]).exists(), "Destination already exists"
            )
            require(file_hash(payload["source"]) == payload["sha256"], "Source hash differs")
            payload["bytes"] = Path(payload["source"]).stat().st_size
        require(not relative(root, item["frame_map_ref"]).parent.exists(), "Frame folder collision")
        require(not relative(root, item["record_dir"]).exists(), "Record folder collision")
        plans.append(
            {
                "item": item,
                "mapping": mapping,
                "files": files,
                "source_revision_directory": str(revision.parent),
                "checked_at_kst": now(),
                "source_metadata": {
                    str(p): file_hash(p)
                    for p in [
                        source_root / "frame-map.private.json",
                        inputs / "input-manifest.json",
                        inputs / "source-map.private.json",
                        source_root / "run-config.json",
                    ]
                },
            }
        )
    return plans


def verify_import(root, plan):
    item = plan["item"]
    mapping = verify_prepared(root, item)
    paths = revision_paths(root, item)
    require(len(paths) == 1, "Import must contain one preserved revision")
    record = read(paths[0])
    validate_draft(record, {**mapping, "sample_id": item["video_id"]}, record_frame_map_ref(item))
    checks = []
    for payload in plan["files"]:
        source_hash = file_hash(payload["source"])
        destination_hash = file_hash(relative(root, payload["destination"]))
        require(source_hash == destination_hash == payload["sha256"], "Transfer bytes changed")
        checks.append(
            {**payload, "source_after_sha256": source_hash, "destination_sha256": destination_hash}
        )
    return {
        "video_id": item["video_id"],
        "verified_at_kst": now(),
        "status": "PASS",
        "revision": paths[0].name,
        "frame_count": mapping["frame_count"],
        "files": checks,
    }


def import_one(root, plan, run):
    """Recheck immediately, copy exclusively, verify, then append the index entry."""
    root = Path(root).resolve()
    item, sid = plan["item"], plan["item"]["video_id"]
    require(
        run.meta["run_kind"] == "MIGRATION" and sid in run.meta["video_ids"], "Wrong import run"
    )
    with writer_lock(root / "service"), operation(run, f"import-{sid}", "PREPARATION", sid):
        revisions = sorted(Path(plan["source_revision_directory"]).glob("revision-*.json"))
        require(
            revisions[-1].name == item["imported_revision"]["filename"], "Latest revision changed"
        )
        for path, sha in plan["source_metadata"].items():
            require(file_hash(path) == sha, "Source metadata changed since preflight")
        # Check all payloads before writing any of this video's files.
        for payload in plan["files"]:
            require(
                file_hash(payload["source"]) == payload["sha256"], "Source changed since preflight"
            )
        rechecked_at = now()
        for payload in plan["files"]:
            target = relative(root, payload["destination"])
            if target.exists():
                require(file_hash(target) == payload["sha256"], "Existing import payload differs")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with Path(payload["source"]).open("rb") as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            require(file_hash(target) == payload["sha256"], "Copied payload differs")
        map_path = relative(root, item["frame_map_ref"])
        if map_path.exists():
            require(read(map_path) == plan["mapping"], "Existing imported map differs")
        else:
            atomic_json(map_path, plan["mapping"], immutable=True)
        result = verify_import(root, plan)
        with writer_lock(root / "manifests"):
            index = root / "manifests/dataset-index.jsonl"
            previous = index.read_bytes() if index.exists() else b""
            entries = [
                json.loads(v) for v in previous.decode("utf-8-sig").splitlines() if v.strip()
            ]
            matching = next((v for v in entries if v["video_id"] == sid), None)
            if matching:
                require(matching == item, "Existing imported index differs")
            else:
                for other in entries:
                    require(
                        other["source_sha256"] != item["source_sha256"]
                        and not any(
                            other[k].casefold() == item[k].casefold()
                            for k in ("source_path", "frame_map_ref", "record_dir")
                        ),
                        "Index collision",
                    )
                pending = index.with_suffix(".pending.jsonl")
                with pending.open("xb") as out:
                    out.write(
                        previous
                        + (b"\n" if previous and not previous.endswith(b"\n") else b"")
                        + json.dumps(item, ensure_ascii=False).encode("utf-8")
                        + b"\n"
                    )
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(pending, index)
        result["revision_rechecked_at_kst"] = rechecked_at
        atomic_json(run.directory / f"transfer-{sid}.json", result)
        return result
