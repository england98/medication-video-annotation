"""Read-only event-spot viewer on synthetic records; bytes stand in for real media."""

import hashlib
import json
import threading
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from medication_video_annotation.spot_viewer import load_catalog, make_viewer_server

CURATED = "data_curated/복약행동_분류"


def add_record(label_root, video_id, locator, content, revision=0, judgment="UNCERTAIN"):
    record = {
        "format": "medication-event-spot-label-v1",
        "video_id": video_id,
        "revision": revision,
        "source": {
            "locator": locator,
            "sha256": hashlib.sha256(content).hexdigest(),
            "duration_ms": 1000,
            "first_source_ms": 0,
        },
        "time_axis": "PLAYER_ELAPSED_MS",
        "target_id": "TARGET-1",
        "events": [],
        "observed_counts": {"PREPARATION": 0, "MEDICATION_FORM": 0, "DRINKING": 0},
        "counts_complete": False,
        "video_judgment": judgment,
        "observation": {"mode": "SAMPLED_TARGETED", "note": "synthetic"},
        "review": {"status": "AI_DRAFT"},
        "development_exposure": "NOT_KNOWN_EXPOSED",
    }
    folder = label_root / "records" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"revision-{revision:06d}.json").write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def spot(tmp_path):
    """Curated and raw-located videos under one data root, plus their label root."""
    data_root, label_root = tmp_path / "data", tmp_path / "labels"
    for group, folder in (
        ("01_핵심타깃", "A003_복약"),
        ("01_핵심타깃", "A004_물마시기"),
        ("03_기타샘플", "A007_과일깎기"),
    ):
        (data_root / CURATED / group / folder).mkdir(parents=True)
    raw = data_root / "data_raw/P033"
    raw.mkdir(parents=True)
    videos = {
        "SRC-MED": (f"{CURATED}/01_핵심타깃/A003_복약/robot3d__A003_P001_G001_C001.mp4", b"m" * 400),
        "SRC-RAW": ("data_raw/P033/A007_P033_G001_C003.mp4", b"r" * 100),
    }
    for video_id, (locator, content) in videos.items():
        (data_root / locator).write_bytes(content)
        add_record(label_root, video_id, locator, content)
    run = label_root / "runs/spot-synthetic"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps(
            {
                "started_at_kst": "2026-01-01T00:00:00+09:00",
                "data_root": str(data_root),
                "entries": [{"video_id": "SRC-MED", "batch_purpose": "EVALUATION_SELECTED"}],
            }
        ),
        encoding="utf-8",
    )
    return data_root, label_root, videos


@pytest.fixture
def base_url(spot):
    server = make_viewer_server(spot[1], 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_behavior_comes_from_folder_taxonomy_even_for_raw_paths(spot):
    catalog, paths = load_catalog(spot[1])
    assert [(b["code"], b["name"], b["group"], b["count"]) for b in catalog["behaviors"]] == [
        ("A003", "복약", "핵심타깃", 1),
        ("A007", "과일깎기", "기타샘플", 1),
    ]
    by_id = {v["video_id"]: v for v in catalog["videos"]}
    assert by_id["SRC-RAW"]["behavior_code"] == "A007"
    assert by_id["SRC-MED"]["batch_purpose"] == "EVALUATION_SELECTED"
    assert set(paths) == {"SRC-MED", "SRC-RAW"}


def test_latest_revision_is_shown(spot):
    _, label_root, videos = spot
    locator, content = videos["SRC-MED"]
    add_record(label_root, "SRC-MED", locator, content, revision=1, judgment="CONFIRMED")
    catalog, _ = load_catalog(label_root)
    video = next(v for v in catalog["videos"] if v["video_id"] == "SRC-MED")
    assert video["revision_file"] == "revision-000001.json"
    assert video["record"]["video_judgment"] == "CONFIRMED"


def test_changed_or_missing_source_is_flagged_and_not_served(spot):
    data_root, label_root, videos = spot
    (data_root / videos["SRC-MED"][0]).write_bytes(b"changed")
    (data_root / videos["SRC-RAW"][0]).unlink()
    catalog, paths = load_catalog(label_root)
    assert {v["video_id"]: v["source_status"] for v in catalog["videos"]} == {
        "SRC-MED": "HASH_MISMATCH",
        "SRC-RAW": "MISSING",
    }
    assert paths == {}


@pytest.mark.parametrize("locator", ["../outside.mp4", "C:/outside.mp4"])
def test_locator_cannot_escape_data_root(spot, locator):
    _, label_root, _ = spot
    add_record(label_root, "SRC-BAD", locator, b"x")
    with pytest.raises(ValueError):
        load_catalog(label_root)


def test_unsupported_label_format_is_refused(spot):
    _, label_root, _ = spot
    path = label_root / "records/SRC-MED/revision-000000.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, "format": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported label format"):
        load_catalog(label_root)


def test_server_serves_catalog_page_and_byte_ranges(spot, base_url):
    with urlopen(base_url + "/") as response:
        assert "이벤트 라벨 확인" in response.read().decode("utf-8")
    with urlopen(base_url + "/api/catalog") as response:
        assert len(json.load(response)["videos"]) == 2
    request = Request(base_url + "/api/video/SRC-MED", headers={"Range": "bytes=10-19"})
    with urlopen(request) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == "bytes 10-19/400"
        assert response.read() == b"m" * 10


def test_server_is_read_only_and_loopback_only(spot, base_url):
    before = sorted(p for p in spot[1].rglob("*"))
    with pytest.raises(HTTPError) as unknown:
        urlopen(base_url + "/api/video/SRC-UNKNOWN")
    assert unknown.value.code == 404
    with pytest.raises(HTTPError) as outside:
        urlopen(base_url + "/api/video/..%2F..%2Fsummary.json")
    assert outside.value.code == 404
    for method in ("POST", "PUT", "DELETE"):
        with pytest.raises(HTTPError) as blocked:
            urlopen(Request(base_url + "/api/catalog", data=b"{}", method=method))
        assert blocked.value.code == 501
    host, port = base_url.removeprefix("http://").split(":")
    connection = HTTPConnection(host, int(port))
    connection.request("GET", "/api/catalog", headers={"Host": "evil.example"})
    assert connection.getresponse().status == 403
    connection.close()
    assert sorted(p for p in spot[1].rglob("*")) == before
