"""Small file primitives; immutable publication and fail-closed writer locks."""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def timestamp(value):
    result = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value))
    if result.utcoffset() is None:
        raise ValueError("Explicit timezone required")
    return result


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value),
        "Invalid stable identifier",
    )
    return value


def relative(root, value):
    require(
        isinstance(value, str) and value and "\\" not in value and ":" not in value,
        "Use a portable DATA_ROOT-relative POSIX path",
    )
    path = PurePosixPath(value)
    require(
        not path.is_absolute() and ".." not in path.parts and str(path) == value,
        "Invalid relative path",
    )
    resolved = (Path(root) / value).resolve()
    require(resolved.is_relative_to(Path(root).resolve()), "Path escapes DATA_ROOT")
    return resolved


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value, *, immutable=False):
    path = Path(path)
    payload = encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        if immutable:
            if os.name == "nt":
                os.rename(temporary, path)  # Windows: atomic and refuses existing targets.
            else:
                os.link(temporary, path)  # POSIX rename would overwrite existing targets.
        else:
            # Windows readers/scanners can briefly deny atomic replacement. Keep
            # the old file intact and retry only this publication, never the work.
            for attempt in range(5):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as error:
                    if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                        raise
                    delay = 0.05 * (2**attempt)
                    logging.getLogger(__name__).warning(
                        "Atomic replace retry %s/4 for %s: %s; wait %.2fs",
                        attempt + 1, path, error, delay,
                    )
                    time.sleep(delay)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def writer_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".writer.lock"
    try:
        with path.open("x", encoding="utf-8") as out:
            out.write(json.dumps({"pid": os.getpid(), "created_at_kst": now()}))
    except FileExistsError as error:
        raise ValueError(
            "Writer lock exists; verify owner stopped before explicit recovery"
        ) from error
    try:
        yield
    finally:
        path.unlink()
