"""Transient publication failures must preserve existing data and fail closed."""

import pytest

from medication_video_annotation import io


def denied(code=32):
    error = PermissionError("injected sharing/access failure")
    error.winerror = code
    return error


def test_transient_replace_preserves_old_until_success(tmp_path, monkeypatch, caplog):
    target = tmp_path / "state.json"
    io.atomic_json(target, {"version": 1})
    original = io.os.replace
    attempts, delays = [], []

    def replace(source, destination):
        attempts.append(source)
        assert io.read(target) == {"version": 1}
        if len(attempts) < 3:
            raise denied()
        original(source, destination)

    monkeypatch.setattr(io.os, "replace", replace)
    monkeypatch.setattr(io.time, "sleep", delays.append)
    io.atomic_json(target, {"version": 2})
    assert io.read(target) == {"version": 2}
    assert len(set(attempts)) == 1 and delays == [0.05, 0.1]
    assert len(caplog.records) == 2
    assert not list(tmp_path.glob(".pending-*"))


@pytest.mark.parametrize("code, calls", [(5, 5), (32, 5), (33, 5), (None, 1)])
def test_persistent_or_other_error_preserves_old(tmp_path, monkeypatch, code, calls):
    target = tmp_path / "state.json"
    io.atomic_json(target, {"version": 1})
    attempts = []

    def replace(*args):
        attempts.append(args)
        raise denied(code)

    monkeypatch.setattr(io.os, "replace", replace)
    monkeypatch.setattr(io.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        io.atomic_json(target, {"version": 2})
    assert len(attempts) == calls
    assert io.read(target) == {"version": 1}
    assert not list(tmp_path.glob(".pending-*"))


def test_immutable_publication_never_uses_replace(tmp_path, monkeypatch):
    target = tmp_path / "revision.json"
    io.atomic_json(target, {"version": 1}, immutable=True)

    def forbidden(*args):
        pytest.fail("Immutable publication must not use replace")

    monkeypatch.setattr(io.os, "replace", forbidden)
    with pytest.raises(FileExistsError):
        io.atomic_json(target, {"version": 2}, immutable=True)
    assert io.read(target) == {"version": 1}
