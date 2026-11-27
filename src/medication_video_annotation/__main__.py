"""Local preparation/review, review-button exports and explicitly selected DEV handoffs."""

import argparse
import json
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import tomli

from .demo import create_demo
from .io import read, require, writer_lock
from .media import prepare_selection, verify_prepared
from .records import load_index, record_frame_map_ref
from .server import Workbench, make_server
from .timing import Run
from .workflow import latest, read_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", type=Path, default=Path("local/synthetic-demo"))
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--selection", type=Path, required=True)
    prepare.add_argument("--run", type=Path, required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--run", type=Path, required=True)
    serve.add_argument("--ids", nargs="+", required=True)
    serve.add_argument("--port", type=int)
    review = sub.add_parser("review", help="Open existing videos with a separate review ledger")
    selection = review.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", dest="all_videos", action="store_true")
    selection.add_argument("--ids", nargs="+")
    selection.add_argument("--from-run", type=Path)
    review.add_argument("--port", type=int)
    stop = sub.add_parser("stop")
    stop.add_argument("--port", type=int)
    spot_view = sub.add_parser("spot-view", help="Read-only viewer for event-spot draft labels")
    spot_view.add_argument("--port", type=int, default=8769)
    spot_view.add_argument("--no-open", action="store_true", help="Do not open a browser")
    author = sub.add_parser("author")
    author.add_argument("--run", type=Path, required=True)
    author.add_argument("--id", required=True)
    author.add_argument("--actor", required=True)
    author.add_argument("--profile", help="Start/continue a semantic profile, e.g. "
                        "onc110-behavior-annotation-v0.2; omitted keeps the v0.1 meaning")
    validate = sub.add_parser("validate")
    validate.add_argument("--ids", nargs="+", required=True)
    for name in ("save", "checkpoint"):
        command = sub.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--actor", required=True)
        command.add_argument("--base-hash")
        command.add_argument("--run", type=Path, required=True)
        if name == "checkpoint":
            command.add_argument("--checkpoint-hash")
            command.add_argument("--cursor", type=int, required=True)
            command.add_argument("--note", default="")
    release = sub.add_parser("release-check")
    release.add_argument("--release", type=Path, required=True)
    handoff = sub.add_parser("dev-handoff-publish", help="Publish selected reviewed DEV snapshots")
    handoff.add_argument("--selection", type=Path, required=True)
    handoff.add_argument("--request", choices=["EXPLICIT_USER_DEV_HANDOFF"], required=True)
    handoff.add_argument("--run", type=Path, required=True)
    handoff_check = sub.add_parser("dev-handoff-check", help="Read fixed DEV snapshots; no ledger writes")
    handoff_check.add_argument("--handoff", type=Path, required=True)
    handoff_check.add_argument("--manifest-sha256", required=True)
    handoff_check.add_argument("--expect-ids", nargs="+", required=True)
    args = parser.parse_args()
    config = {}
    if args.config:
        with args.config.open("rb") as source:
            config = tomli.load(source)
        require(config.get("config_version") == "0.1", "Unsupported config version")
        require(
            not config.get("review", {}).get("cvat_enabled", False)
            and not config.get("exports", {}).get("automatic", False),
            "Unsupported integration/automatic export",
        )
        with (Path(__file__).resolve().parents[2] / "configs/project.example.toml").open(
            "rb"
        ) as source:
            policy = tomli.load(source)
        for group in ("observation", "records", "exports", "timing", "review"):
            for key, value in config.get(group, {}).items():
                if (group, key) != ("review", "port"):
                    require(
                        key in policy[group] and value == policy[group][key],
                        f"Unsupported policy override: {group}.{key}",
                    )
    project = Path(__file__).resolve().parents[2]
    root = args.data_root or project / config.get("paths", {}).get("data_root", "local")
    root = root.resolve()
    if args.command in {"prepare", "save", "checkpoint", "author"}:
        require(
            not (root / "service/.writer.lock").exists(),
            "Stop this data root's review server before CLI writes; use the review UI while serving",
        )
    if args.command == "demo":
        result = create_demo(args.output)
    elif args.command == "spot-view":
        from .spot_viewer import make_viewer_server

        server = make_viewer_server(root / "event-spot", args.port)
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"{url} | read-only | Ctrl+C to stop", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    elif args.command == "author":
        from .authoring import session

        session(root, args.run, args.id, args.actor, args.profile)
        return
    elif args.command == "prepare":
        with writer_lock(root / "service"):
            result = prepare_selection(root, read(args.selection), Run(args.run))
    elif args.command in {"serve", "review"}:
        port = args.port or config.get("review", {}).get("port", 8767)
        with writer_lock(root / "service"):
            selection = None
            if args.command == "review":
                from .review_session import review_session

                run_path, ids, selection = review_session(
                    root, all_videos=args.all_videos, ids=args.ids, from_run=args.from_run
                )
            else:
                run_path, ids = args.run, args.ids
            print(f"Checking {len(ids)} videos; existing media/frames are reused...", flush=True)
            server = make_server(root, run_path, ids, port, selection=selection)
            print(
                f"http://127.0.0.1:{server.server_port} | DATA_ROOT={root} | Ctrl+C to stop",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        return
    elif args.command == "stop":
        port = args.port or config.get("review", {}).get("port", 8767)
        address = f"http://127.0.0.1:{port}"
        with urlopen(address + "/api/index", timeout=5) as response:
            info = json.load(response)
        require(
            info.get("application") == "medication-video-annotation"
            and Path(info["data_root"]).resolve() == root,
            "Refuse to stop a different application/data root",
        )
        request = Request(
            address + "/api/control/stop",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Workbench-Token": info["token"]},
        )
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(address + "/api/index", timeout=1) as response:
                    require(
                        json.load(response).get("token") == info["token"],
                        "Another server started while stopping",
                    )
            except (URLError, ConnectionError, TimeoutError):
                result["status"] = "STOPPED"
                break
            time.sleep(0.05)
        require(result["status"] == "STOPPED", "Server did not stop within 10 seconds")
    elif args.command == "release-check":
        result = {"status": "PASS", "records": len(read_release(root, read(args.release)))}
    elif args.command == "dev-handoff-publish":
        from .dev_handoff import publish_dev_handoff
        from .operations import operation

        run = Run(args.run)
        require(run.meta["run_kind"] == "SETUP", "DEV publication uses a separate SETUP run")
        with operation(run, "publish-dev-handoff", "SETUP_HANDOFF"):
            result = publish_dev_handoff(root, read(args.selection), request=args.request)
    elif args.command == "dev-handoff-check":
        from .dev_handoff import read_dev_handoff

        checked = read_dev_handoff(
            args.handoff, media_root=root, expected_manifest_sha256=args.manifest_sha256,
            expected_ids=args.expect_ids,
        )
        result = {"status": "PASS", "format": checked["manifest"]["format"],
                  "handoff_id": checked["manifest"]["handoff_id"],
                  "manifest_sha256": checked["manifest_sha256"],
                  "ids": checked["manifest"]["selected_ids"], "records": len(checked["records"])}
    else:
        items = {v["video_id"]: v for v in load_index(root)}
        if args.command == "validate":
            require(set(args.ids) <= set(items), "Unknown selected ID")
            for sid in args.ids:
                mapping = verify_prepared(root, items[sid])
                record, _ = latest(root, items[sid])
                if record:
                    from .records import validate_draft

                    validate_draft(
                        record, {**mapping, "sample_id": sid}, record_frame_map_ref(items[sid])
                    )
            result = {"status": "PASS", "ids": args.ids}
        else:
            bench = Workbench(root, args.run, [args.id])
            value = {
                "record": read(args.input),
                "base_hash": args.base_hash,
                "actor_ref": args.actor,
            }
            if args.command == "checkpoint":
                value.update(
                    checkpoint_hash=args.checkpoint_hash, cursor=args.cursor, note=args.note
                )
            with writer_lock(root / "service"):
                result = bench.write(args.command, args.id, value)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
