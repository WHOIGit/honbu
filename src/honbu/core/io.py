"""Atomic artifact writes. Every framework-written file goes through here.

The window this closes is not a race on interleaved bytes — it is truncation. The
naive pattern is ``open(path, "w")``, which truncates *before* writing, so any
reader in that window sees an empty file rather than either version. A run
manifest is rewritten on start, on every artifact update, and again on completion
or failure, and it is read by inspection, comparison, and resume, so that window
is worth five lines to remove.

Deliberately no ``fsync``. Fsync protects against the *machine* dying, not the
process: a Python exception, a segfault, even SIGKILL leaves the bytes in the
kernel's page cache to be written out. The realistic loss it prevents is one
manifest update during a power failure, which is not worth a device round trip on
every write.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

_TEMP_ATTEMPTS = 8


def _create_temp(parent: Path, name: str) -> tuple[int, Path]:
    """A uniquely named temp file beside the destination.

    ``O_EXCL`` makes the name unique by construction, and unlike ``mkstemp`` it
    respects the process umask — ``mkstemp`` hardcodes 0600, which would silently
    make every artifact owner-only on a shared filesystem.
    """
    for _ in range(_TEMP_ATTEMPTS):
        candidate = parent / f".{name}.{secrets.token_hex(6)}.tmp"
        try:
            return os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644), candidate
        except FileExistsError:
            continue
    raise OSError(f"could not create a temporary file beside {parent / name}")


def atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``, leaving no residue if the write fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temporary = _create_temp(path.parent, path.name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        # BaseException, not Exception: KeyboardInterrupt mid-write should not
        # leave a hidden .tmp behind to make the directory look non-empty later.
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    """Human-readable artifact JSON. NOT the canonical encoder.

    Deliberately a different serializer, and the two must never share one: this is
    indented, insertion-ordered (a class map's numeric-string keys carry meaning in
    their order), and leaves non-ASCII as UTF-8. Nothing hashed passes through
    here.
    """
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
