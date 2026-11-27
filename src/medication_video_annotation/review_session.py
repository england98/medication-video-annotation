"""Select existing videos and reuse an independent, open human-review ledger."""

import uuid
from pathlib import Path

from .io import atomic_json, digest_bytes, encoded, read, require
from .records import load_index
from .timing import Run, create_run


def review_session(root, *, all_videos=False, ids=None, from_run=None):
    """Called under the DATA_ROOT service lock; never edits the source work run."""
    root = Path(root).resolve()
    require(
        sum((bool(all_videos), ids is not None, from_run is not None)) == 1,
        "Choose exactly one of --all, --ids or --from-run",
    )
    available = [item["video_id"] for item in load_index(root)]
    source = None
    if from_run is not None:
        source = Run(from_run)
        source._refresh_targets()
        require(
            source.meta["run_kind"] in {"TRIAL", "MIGRATION"},
            "--from-run requires an analysis or migration run",
        )
        selected = list(source.meta["video_ids"])
        mode = "batch"
    else:
        selected = available if all_videos else list(ids)
        mode = "all" if all_videos else "selected"
    require(selected and len(selected) == len(set(selected)), "Empty/duplicate selected IDs")
    require(set(selected) <= set(available), "Unknown selected ID")
    selection = {
        "mode": mode,
        "ids": selected,
        "source_run": str(source.directory.resolve()) if source else None,
    }
    # Membership defines the ledger; changing dropdown order does not lose review timers.
    key = digest_bytes(encoded({**selection, "ids": sorted(selected)}))[:20]
    for path in sorted((root / "runs").glob(f"review-{key}-*"), reverse=True):
        if not (path / "selection.json").exists():
            continue
        run = Run(path)
        saved = read(path / "selection.json")
        require(
            {**saved, "ids": sorted(saved["ids"])} == {**selection, "ids": sorted(selected)},
            "Review selection identity changed",
        )
        require(
            run.meta["run_kind"] == "REVIEW" and set(run.meta["video_ids"]) == set(selected),
            "Review ledger targets changed",
        )
        if not any(e["event"] == "RUN_END" for e in run.events()):
            return path, selected, selection
    path = create_run(
        root / "runs", f"review-{key}-{uuid.uuid4().hex[:8]}", "REVIEW", video_ids=selected
    )
    atomic_json(path / "selection.json", selection, immutable=True)
    return path, selected, selection
