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
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from honbu.core.io import atomic_write_json, read_json
from honbu.core.targets import MANIFEST_NAME, RESUMABLE_STATES
from honbu.errors import RefusalError
from honbu.sweeps.prepare import read_sweep_index

#: The sweep's progress cache, beside its ``config/`` rather than inside it.
SWEEP_MANIFEST_NAME = "manifest.json"

#: What a child's state is when its run directory is not there.
MISSING = "missing"

ErrorPolicy = Literal["stop", "continue"]


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
        """Whether an execution pass should pick this child up."""
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
        """Active (in-definition) children whose run directory is missing."""
        return tuple(c for c in self.current if c.missing)

    @property
    def retired(self) -> tuple[ChildStatus, ...]:
        return tuple(c for c in self.children if not c.in_definition)

    @property
    def retired_missing(self) -> tuple[ChildStatus, ...]:
        """Retired children whose run directory is missing, tracked separately."""
        return tuple(c for c in self.retired if c.missing)

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
    """Rebuild a sweep's whole state from the index and the children."""
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
    """Persist the cache, stamped with when it was taken."""
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
    """The cache exactly as it sits on disk, stale or not, or None if absent."""
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
#: process, as a subprocess, or as a submitted job.
RunChild = Callable[[ChildStatus], Any]


def child_argv(
    child: ChildStatus,
    *,
    resolved_flag: str = "--resolved",
    resume_flag: str = "--resume",
) -> list[str]:
    """Uniform resume invocation for one child, with parameterized flag spellings."""
    return [resolved_flag, str(child.run_dir), resume_flag]


@dataclass(frozen=True)
class FailedChild:
    """One child whose runner callback raised under ``on_error='continue'``."""

    child: ChildStatus
    error: Exception

    @property
    def index(self) -> int:
        return self.child.index


@dataclass(frozen=True)
class ExecutionResult:
    """What one execution pass did."""

    ran: tuple[ChildStatus, ...]
    skipped: tuple[ChildStatus, ...]
    missing: tuple[ChildStatus, ...]
    failed: tuple[FailedChild, ...] = ()


def _resolve_only_selectors(
    only: Sequence[int | str], active: Sequence[ChildStatus]
) -> set[int]:
    """Resolve child indices or gate values against active children, refusing unknowns."""
    by_index = {child.index: child.index for child in active}
    by_gate: dict[Any, list[int]] = {}
    by_gate_str: dict[str, list[int]] = {}
    for child in active:
        by_gate.setdefault(child.gate_value, []).append(child.index)
        by_gate_str.setdefault(str(child.gate_value), []).append(child.index)

    selected: set[int] = set()
    unknown: list[int | str] = []
    for selector in only:
        if isinstance(selector, int) and not isinstance(selector, bool):
            if selector in by_index:
                selected.add(selector)
                continue
            if selector in by_gate:
                selected.update(by_gate[selector])
                continue
            unknown.append(selector)
        else:
            if selector in by_gate:
                selected.update(by_gate[selector])
                continue
            if str(selector) in by_gate_str:
                selected.update(by_gate_str[str(selector)])
                continue
            unknown.append(selector)

    if unknown:
        indices = [child.index for child in active]
        gates = [child.gate_value for child in active]
        raise RefusalError(
            f"unknown child selector(s) in only=: {unknown}; "
            f"active indices: {indices}, gates: {gates}"
        )
    return selected


def execute_sweep(
    sweep_dir: Path,
    run_child: RunChild,
    *,
    only: Sequence[int | str] | None = None,
    on_error: ErrorPolicy = "stop",
) -> ExecutionResult:
    """Run every outstanding child, holding no state of its own.

    ``on_error`` defaults to ``"stop"`` (propagating the first child exception);
    pass ``on_error="continue"`` to record per-child exceptions in
    ``ExecutionResult.failed`` and continue running remaining siblings while
    always propagating ``KeyboardInterrupt`` and ``SystemExit``.

    ``only`` accepts child indices or gate values and refuses unknown selectors.
    """
    if on_error not in ("stop", "continue"):
        raise RefusalError(f"on_error must be 'stop' or 'continue', not {on_error!r}")

    status = refresh_sweep(sweep_dir)
    wanted = _resolve_only_selectors(only, status.current) if only is not None else None

    ran: list[ChildStatus] = []
    skipped: list[ChildStatus] = []
    missing: list[ChildStatus] = []
    failed: list[FailedChild] = []

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
            if on_error == "continue":
                try:
                    run_child(child)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    failed.append(FailedChild(child=child, error=exc))
                else:
                    ran.append(child)
            else:
                run_child(child)
                ran.append(child)
    finally:
        refresh_sweep(sweep_dir)

    return ExecutionResult(
        ran=tuple(ran),
        skipped=tuple(skipped),
        missing=tuple(missing),
        failed=tuple(failed),
    )
