"""Pure five-file serialization; no directories, writes, UI or export command."""

from .behavior_profile import require_legacy
from .io import digest_bytes, encoded, require
from .legacy_record import events_csv


def bundle_bytes(record, *, revision_ref, revision_file_sha256, exported_at_kst, purpose):
    require(purpose, "Export purpose required")
    require_legacy(record, "Five-file export v0.1")
    files = {
        "record.json": encoded(record),
        "analysis.json": encoded(record["analysis"]),
        "label.json": encoded(record["label"]),
        "events.csv": events_csv(record).encode("utf-8-sig"),
    }
    manifest = {
        "format": "onc110-export-manifest-v0.1",
        "exported_at_kst": exported_at_kst,
        "purpose": purpose,
        "video_id": record["video_id"],
        "source_sha256": record["label"]["source_sha256"],
        "revision_ref": revision_ref,
        "revision_file_sha256": revision_file_sha256,
        "record_format": record["format"],
        "label_format": record["label"]["format"],
        "review": record["label"]["review"],
        "annotation_status": record["label"]["coverage"]["annotation_status"],
        "final_ground_truth": record["label"]["final_ground_truth"],
        "evaluation_eligible": record["label"]["evaluation_eligible"],
        "files": {
            name: {"sha256": digest_bytes(data), "bytes": len(data)} for name, data in files.items()
        },
    }
    files["export-manifest.json"] = encoded(manifest)
    return files
