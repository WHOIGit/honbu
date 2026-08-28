"""Filesystem-safe path components, sanitized the same way on every platform.

Run directories and artifact subdirectories are named from configuration values —
an experiment name, a run id, a head name. The legal character set for a path
component is filesystem-dependent, so a naive implementation sanitizes to the
running platform's rules and a single configuration then produces *different*
directory names on different machines. That is unacceptable for configuration
defaults committed to a repository, so Honbu sanitizes to the **union** of POSIX
and Windows rules everywhere: forbidden characters, control characters, reserved
device stems, and trailing dots and spaces.

The cost is that a name legal on POSIX may still be altered. That cost is paid
visibly: :func:`sanitization_warning` reports every change, so a rename surfaces
once at resolution rather than as a mystery directory later.

Sanitized strings never carry identity. Path-derived digests use
separator-normalized values computed independently of this module, so two
platforms agree on a hash even where their path components would differ.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

# Windows reserves the DOS device names in every directory, with or without an
# extension ("NUL.txt" is as unusable as "NUL").
_RESERVED_STEMS: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)

# The union: POSIX forbids only "/" and NUL; Windows adds these and the C0
# controls. Colons are included, so a value carrying one is altered everywhere
# rather than only where the filesystem insists.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_REPLACEMENT: Final = "_"

#: Windows MAX_PATH is 259 usable characters, and several directory-creating APIs
#: cap lower still. Honbu creates only the run directory; a project then writes
#: nested artifacts beneath it, so the budget leaves room for those.
WINDOWS_RUN_DIR_BUDGET: Final = 180


def sanitize_path_component(name: str) -> str:
    """Return ``name`` made usable as a single path component on any platform."""
    cleaned = _FORBIDDEN.sub(_REPLACEMENT, name)
    # Windows silently drops trailing dots and spaces when creating the entry, so
    # a name ending in one resolves to a *different* directory than requested.
    cleaned = cleaned.rstrip(". ")
    if cleaned.split(".", 1)[0].upper() in _RESERVED_STEMS:
        cleaned = f"{_REPLACEMENT}{cleaned}"
    # "." and ".." are directory references on both platforms, never names.
    if cleaned in {"", ".", ".."}:
        return _REPLACEMENT
    return cleaned


def sanitize_relative_path(value: str) -> str:
    """Sanitize each segment of ``value``, keeping the joins.

    For values that *become* path components, including ones that intentionally
    nest (``experiment.name: p2/08_multihead``). Never for a whole path: empty
    segments are dropped, so an absolute path passed here would come back
    relative. Whole paths are exempted at the call site — by the portable-path
    marker on a configuration field, or by a template function declaring that it
    emits a path.
    """
    segments = [
        sanitize_path_component(segment)
        for segment in value.replace("\\", "/").split("/")
        if segment != ""
    ]
    return "/".join(segments) if segments else _REPLACEMENT


def sanitization_warning(original: str, emitted: str) -> str | None:
    """A warning when sanitization altered a value, so the rename is never silent."""
    if original == emitted:
        return None
    return (
        f"path component {original!r} is not portable and was written as "
        f"{emitted!r} on every platform"
    )


def is_absolute_uri(uri: str) -> bool:
    """Whether ``uri`` already addresses a location on its own.

    True for schemed URIs and for absolute paths in *either* platform's syntax, so
    a reference recorded on Windows (``C:\\data\\a.png``) is not mistaken for a
    relative reference when read on POSIX, or the reverse.
    """
    if "://" in uri:
        return True
    return PurePosixPath(uri).is_absolute() or PureWindowsPath(uri).is_absolute()


def is_windows() -> bool:
    """The single place the platform is detected, so tests can flip it in one place."""
    return os.name == "nt"


def windows_long_paths_enabled() -> bool:
    """Whether this Windows host has the MAX_PATH limit lifted.

    CPython's manifest has declared long-path awareness since 3.6, so the registry
    flag is the operative gate.
    """
    if not is_windows():
        return True
    import winreg  # noqa: PLC0415 — Windows-only import

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def path_length_refusal(path: Path, *, budget: int = WINDOWS_RUN_DIR_BUDGET) -> str | None:
    """A refusal message when ``path`` leaves no room for artifacts beneath it.

    Refused, never truncated, and checked before anything is written. Only fires on
    a platform that enforces a limit — from POSIX there is no way to know the
    target Windows host's setting, which is what a ``--target-platform`` preview
    would be for.
    """
    if not is_windows() or windows_long_paths_enabled():
        return None
    length = len(str(path))
    if length <= budget:
        return None
    return (
        f"run directory is {length} characters, over the {budget}-character budget "
        f"for this host (long paths are disabled): {path}"
    )


def normalize_separators(value: str) -> str:
    """POSIX separators, for values that feed identity rather than the filesystem."""
    return value.replace("\\", "/")
