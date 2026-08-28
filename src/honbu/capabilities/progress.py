"""A sweep's state, which is its children's states and nothing else.

Every fact about how far a sweep has got lives in the children's manifests. This
module reads them and adds up. Nothing here is authoritative, and nothing here is
written by anything that could disagree with a child — the one-writer rule that
makes a run's manifest trustworthy is exactly what makes a *sweep's* progress a
derived quantity rather than a stored one.

So the sweep's record is split across two files, deliberately, where a run's is
deliberately one:

* ``config/index.json`` — which children exist and where. Written by preparation,
  accumulating, permanent.
* ``manifest.json`` — the progress **cache**. Rebuildable by rescanning, deletable
  without consequence, and stamped with when it was last refreshed.

A run's manifest keeps its state and its index together because one writer owns
both, and splitting them would let them disagree. Here the opposite holds: the
index and the progress have different writers and different trust levels, and
putting a cache inside a permanent record is an invitation to trust it.

The cache earns its place by being readable without running anything — ``cat`` the
file and see 8/12. It pays for that with a stale-read hazard that has to be visible
rather than papered over: a child run by a scheduler array updates its own manifest
and nothing else, so a count with no timestamp would assert a currency it does not
have. Hence the refresh time, and hence anything that reports refreshes first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from honbu.capabilities.prepare import read_sweep_index
from honbu.mechanics.io import atomic_write_json, read_json
from honbu.mechanics.targets import MANIFEST_NAME, RESUMABLE_STATES

#: The sweep's progress cache, beside its ``config/`` rather than inside it.
SWEEP_MANIFEST_NAME = "manifest.json"

#: What a child's state is when its run directory is not there. Deliberately not
#: None-as-absence: a missing child is a *finding*, and reporting says so rather
#: than leaving it out of a total that then looks complete.
MISSING = "missing"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ChildStatus:
    """One child, as of the last scan."""

    index: int
    gate_value: Any
    run_dir: Path
    values: Mapping[str, Any]
    state: str
    in_definition: bool
    best: Mapping[str, Any] | None = None
    progress: Mapping[str, Any] | None = None

    @property
    def missing(self) -> bool:
        return self.state == MISSING

    @property
    def resumable(self) -> bool:
        """Whether an execution pass should pick this child up.

        The existing run vocabulary is already exactly right, so there is none to
        add: ``completed`` is skipped and everything else is resumed. ``running``
        being resumable is what covers a scheduler timeout, where the child was
        killed without getting to record anything.
        """
        return self.state in RESUMABLE_STATES

    def as_entry(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "gate": self.gate_value,
            "run_dir": str(self.run_dir),
            "state": self.state,
            "in_definition": self.in_definition,
        }


@dataclass(frozen=True)
class SweepStatus:
    """What a sweep looks like right now, derived from its children."""

    sweep_id: str
    sweep_hash: str
    gate: str
    sweep_dir: Path
    children: tuple[ChildStatus, ...]
    refreshed: str

    def __len__(self) -> int:
        return len(self.children)

    @property
    def current(self) -> tuple[ChildStatus, ...]:
        """Children the current definition covers. Retired ones are excluded from
        counts but never from the record."""
        return tuple(child for child in self.children if child.in_definition)

    @property
    def completed(self) -> tuple[ChildStatus, ...]:
        return tuple(c for c in self.current if c.state == "completed")

    @property
    def outstanding(self) -> tuple[ChildStatus, ...]:
        return tuple(c for c in self.current if c.resumable)

    @property
    def missing(self) -> tuple[ChildStatus, ...]:
        return tuple(c for c in self.children if c.missing)

    @property
    def retired(self) -> tuple[ChildStatus, ...]:
        return tuple(c for c in self.children if not c.in_definition)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for child in self.current:
            counts[child.state] = counts.get(child.state, 0) + 1
        return counts

    def summary(self) -> str:
        """One line, the way the cache is meant to be read."""
        done = len(self.completed)
        total = len(self.current)
        parts = [f"{done}/{total} complete"]
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.retired:
            parts.append(f"{len(self.retired)} retired")
        return f"{self.sweep_id}: {', '.join(parts)} (as of {self.refreshed})"


def _read_child(entry: Mapping[str, Any]) -> ChildStatus:
    """One child's state, read from its own manifest and nowhere else."""
    run_dir = Path(entry["run_dir"])
    path = run_dir / MANIFEST_NAME
    state, best, progress = MISSING, None, None
    if path.is_file():
        data = read_json(path)
        if isinstance(data, dict):
            state = str(data.get("state", MISSING))
            best = data.get("best")
            progress = data.get("progress") or None
    return ChildStatus(
        index=int(entry.get("index", -1)),
        gate_value=entry.get("gate"),
        run_dir=run_dir,
        values=dict(entry.get("values", {})),
        state=state,
        in_definition=bool(entry.get("in_definition", True)),
        best=best,
        progress=progress,
    )


def scan_sweep(sweep_dir: Path) -> SweepStatus:
    """Rebuild a sweep's whole state from the index and the children.

    Children are located through the index and never by scanning directories, so a
    child that was moved or deleted is reported missing rather than silently
    omitted. Scanning would find the survivors and report a smaller sweep that
    looked complete.
    """
    index = read_sweep_index(sweep_dir)
    return SweepStatus(
        sweep_id=str(index.get("sweep_id", "")),
        sweep_hash=str(index.get("sweep_hash", "")),
        gate=str(index.get("gate", "")),
        sweep_dir=Path(sweep_dir),
        children=tuple(_read_child(entry) for entry in index.get("children", ())),
        refreshed=_now(),
    )


def write_progress(status: SweepStatus) -> Path:
    """Persist the cache, stamped with when it was taken.

    The stamp is not decoration. Without it the file states a count as though it
    were current, and it is stale precisely when it matters most — while a scheduler
    is running the children and updating nothing but their own manifests.
    """
    path = status.sweep_dir / SWEEP_MANIFEST_NAME
    atomic_write_json(
        path,
        {
            "sweep_id": status.sweep_id,
            "sweep_hash": status.sweep_hash,
            "gate": status.gate,
            "refreshed": status.refreshed,
            "counts": status.counts(),
            "children": [child.as_entry() for child in status.children],
        },
    )
    return path


def read_progress(sweep_dir: Path) -> dict[str, Any] | None:
    """The cache exactly as it sits on disk, stale or not, or None if absent.

    For looking at the file, not for deciding anything. Everything that acts calls
    :func:`refresh_sweep`.
    """
    path = Path(sweep_dir) / SWEEP_MANIFEST_NAME
    if not path.is_file():
        return None
    data = read_json(path)
    return data if isinstance(data, dict) else None


def refresh_sweep(sweep_dir: Path) -> SweepStatus:
    """Rescan and rewrite the cache. What every reporting operation calls first."""
    status = scan_sweep(sweep_dir)
    write_progress(status)
    return status


# --- execution ---------------------------------------------------------------

#: Runs one child. Given its status, it must make that child's run happen — in
#: process, as a subprocess, or as a submitted job. Honbu supplies the argv.
RunChild = Callable[[ChildStatus], Any]


def child_argv(child: ChildStatus) -> list[str]:
    """The one call that runs any child, whatever state it is in.

    A child that never started and a child that died at epoch forty are the same
    invocation. That uniformity is why a driver can be a ``for`` loop, and why a
    scheduler array over the child directories needs no glue: there is nothing to
    decide per child, so there is nothing for a driver to remember.
    """
    return ["--resolved", str(child.run_dir), "--resume"]


@dataclass(frozen=True)
class ExecutionResult:
    """What one execution pass did.

    It does not report where an interrupted pass stopped, because that is not the
    driver's to know: refresh the sweep and the children say so. A driver that
    returned its own account of where it got to would be the state this design
    exists to not have.
    """

    ran: tuple[ChildStatus, ...]
    skipped: tuple[ChildStatus, ...]
    missing: tuple[ChildStatus, ...]


def execute_sweep(
    sweep_dir: Path,
    run_child: RunChild,
    *,
    only: Sequence[int] | None = None,
) -> ExecutionResult:
    """Run every outstanding child, holding no state of its own.

    The driver's entire memory is on disk, in the children's manifests. Kill it at
    any point and start it again: completed children are skipped, the one that was
    interrupted resumes, and the rest run. Nothing is lost and nothing is repeated,
    and that is true without the driver having written anything down — which is what
    makes "stop after this child, I need the GPU" a supported operation rather than
    a feature.

    A missing child is reported, not run and not skipped silently: its directory is
    gone, so there is nothing to resume, and quietly passing over it would let a
    sweep report itself finished while a child it names does not exist.

    Interrupting propagates. A signal is the user's decision, and swallowing it here
    to move to the next child would be the opposite of what was asked.
    """
    status = refresh_sweep(sweep_dir)
    wanted = set(only) if only is not None else None

    ran: list[ChildStatus] = []
    skipped: list[ChildStatus] = []
    missing: list[ChildStatus] = []

    try:
        for child in status.current:
            if wanted is not None and child.index not in wanted:
                continue
            if child.missing:
                missing.append(child)
                continue
            if not child.resumable:
                skipped.append(child)
                continue
            run_child(child)
            ran.append(child)
    finally:
        # Whatever happened — finished, interrupted, or a child raising — the cache
        # is brought up to date before leaving, so the file on disk describes the
        # sweep as it now is rather than as it was when the pass began.
        refresh_sweep(sweep_dir)

    return ExecutionResult(
        ran=tuple(ran), skipped=tuple(skipped), missing=tuple(missing)
    )
