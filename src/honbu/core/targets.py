"""Collision-safe output targets.

Two properties, and neither is depth. A prior implementation refused targets with
fewer than three path parts, which is arbitrary in both directions: it blocks a
legitimate ``/runs`` output root while permitting most of the filesystem.

The first property is **containment** — the thing we are about to clear lies
strictly inside a root the configuration named, and is not that root. The second
is **scope**: what a command may clear comes from the command's declaration, not
from a flag. ``--clobber`` used to mean "clear the run directory" while one
project used it to mean "rewrite the figures"; no flag can span blast radii that
differ by three orders of magnitude, so the declaration supplies the radius and
the flag supplies only permission.

``--force`` is gone. Its value depended entirely on the run-directory template:
with a timestamped default it never fires, and with a deterministic one it fires
on every deliberate re-run and becomes reflex. Describing the occupant protects
the same mistake with information rather than ceremony.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from honbu.errors import RefusalError
from honbu.core.io import read_json

#: The run record. Named here because collision validation reads it to describe
#: what occupies a target before destroying it.
MANIFEST_NAME = "manifest.json"

#: States from which an occupied directory may be resumed rather than cleared.
RESUMABLE_STATES = frozenset(
    {"initialized", "running", "partial", "interrupted", "failed"}
)

_DESCRIBE_LIMIT = 5


def validate_destructive_target(target: Path, *, containment_root: Path) -> Path:
    """Confirm ``target`` is somewhere we may clear.

    Resolving first means a symlinked target is judged by where it actually points,
    so a link out of the containment root is refused rather than followed.
    """
    resolved = target.resolve()
    root = containment_root.resolve()
    if resolved == root:
        raise RefusalError(f"refusing to clear the containment root itself: {root}")
    if not resolved.is_relative_to(root):
        raise RefusalError(
            f"refusing a target outside its containment root: {resolved} (root: {root})"
        )
    return resolved


def read_existing_manifest(target: Path) -> dict[str, Any] | None:
    manifest = target / MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        value = read_json(manifest)
    except (OSError, ValueError):
        return {"state": "unreadable"}
    return value if isinstance(value, dict) else {"state": "unreadable"}


def describe_manifest(manifest: dict[str, Any]) -> str:
    """One line naming what a run is, so a human decides with the facts present."""
    command = manifest.get("command")
    if isinstance(command, list):
        command = " ".join(str(segment) for segment in command)
    parts = [f"{manifest.get('state', 'unknown')} {command or 'unknown'} run"]

    progress = manifest.get("progress")
    if isinstance(progress, dict) and progress.get("epoch") is not None:
        parts.append(f"{progress['epoch']} epochs")

    best = manifest.get("best")
    if isinstance(best, dict) and isinstance(best.get("metrics"), dict):
        rendered = ", ".join(f"{k} {v}" for k, v in list(best["metrics"].items())[:2])
        if rendered:
            parts.append(f"best {rendered}")

    identities = manifest.get("identities")
    if isinstance(identities, dict) and identities.get("config_id"):
        parts.append(f"config_id={identities['config_id']}")

    return ", ".join(parts)


def case_collision(target: Path) -> str | None:
    """A sibling differing only in letter case: two directories here, one on Windows."""
    parent = target.parent
    if not parent.is_dir():
        return None
    lowered = target.name.lower()
    for sibling in parent.iterdir():
        if sibling.name != target.name and sibling.name.lower() == lowered:
            return (
                f"{target.name!r} and existing {sibling.name!r} differ only in case; "
                "these are two directories on this filesystem and one on Windows"
            )
    return None


def clear_contents(target: Path) -> None:
    """Clear the directory's contents, keeping the directory itself.

    Removing and recreating it would discard its permissions and ACLs, and fails
    outright where the parent is not writable.
    """
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _occupant_refusal(resolved: Path, entries: list[str], *, offer: str) -> RefusalError:
    manifest = read_existing_manifest(resolved)
    occupant = f"\n  holds {describe_manifest(manifest)}" if manifest else ""
    return RefusalError(
        f"output target is not empty: {resolved}"
        f"\n  contains: {', '.join(entries[:_DESCRIBE_LIMIT])}"
        f"{occupant}"
        f"\n  {offer}"
    )


def prepare_target(
    target: Path,
    *,
    containment_root: Path,
    clobber: bool = False,
    cli_hint: str | None = None,
) -> tuple[Path, tuple[str, ...]]:
    """Validate, clear if permitted, and create a *producing* command's target."""
    resolved = validate_destructive_target(target, containment_root=containment_root)
    warnings = tuple(warning for warning in (case_collision(resolved),) if warning)

    if resolved.is_file():
        if not clobber:
            raise RefusalError(f"output target exists as a file: {resolved}")
        resolved.unlink()
    elif resolved.is_dir():
        entries = sorted(entry.name for entry in resolved.iterdir())
        if entries and not clobber:
            raise _occupant_refusal(
                resolved,
                entries,
                offer=(
                    cli_hint
                    if cli_hint is not None
                    else "pass clobber=True to replace it, or resume=True to continue it"
                ),
            )
        if entries:
            clear_contents(resolved)

    resolved.mkdir(parents=True, exist_ok=True)
    return resolved, warnings


def prepare_scope(
    run_dir: Path,
    scope: tuple[Path, ...],
    *,
    containment_root: Path,
    clobber: bool = False,
    cli_hint: str | None = None,
) -> tuple[str, ...]:
    """Validate and clear an *augmenting* command's declared leaves.

    The run directory must already exist and hold a manifest — an augmenting
    command adds to a run, it does not conjure one. Each leaf is contained-checked
    independently, so a leaf templated outside the run still cannot escape the
    containment root.
    """
    if not run_dir.is_dir():
        raise RefusalError(f"no run directory to augment: {run_dir}")
    if read_existing_manifest(run_dir) is None:
        raise RefusalError(
            f"{run_dir} holds no {MANIFEST_NAME}; an augmenting command needs an "
            "existing run"
        )

    warnings: list[str] = []
    for leaf in scope:
        resolved = validate_destructive_target(leaf, containment_root=containment_root)
        collision = case_collision(resolved)
        if collision:
            warnings.append(collision)
        if resolved.is_file():
            if not clobber:
                raise RefusalError(f"output leaf exists as a file: {resolved}")
            resolved.unlink()
        elif resolved.is_dir():
            entries = sorted(entry.name for entry in resolved.iterdir())
            if entries and not clobber:
                raise _occupant_refusal(
                    resolved,
                    entries,
                    offer=(
                        cli_hint
                        if cli_hint is not None
                        else "pass clobber=True to replace this leaf"
                    ),
                )
            if entries:
                clear_contents(resolved)
        resolved.mkdir(parents=True, exist_ok=True)
    return tuple(warnings)
