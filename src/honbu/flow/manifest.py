"""The run manifest: one file, written live, holding the run's whole record.

Two jobs, deliberately in one file. It is the *state* record — what happened,
when, and how it ended — and it is the *index* other operations locate resources
through, which is what lets a sweep pull ``artifacts["checkpoint"]`` from every
child without knowing what any of them trained. Splitting those into two files
would reintroduce the problem the one-writer rule exists to prevent.

It is mutable during work, and every mutation flushes atomically. That is the
whole point: an interrupted run must still name the checkpoint it got to. A
manifest written only at completion is a manifest that is absent exactly when you
need it.

What Honbu does *not* do is decide anything. It never picks which epoch was best,
never ranks a metric, never interprets a provenance label. The project calls
:meth:`Manifest.record_best`; Honbu stores what it is given. Anything else would
be prescribing a metric contract, which is a stated non-goal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from honbu.errors import RefusalError
from honbu.mechanics.canonical import CANONICAL_ENCODER_VERSION
from honbu.mechanics.io import atomic_write_json, read_json
from honbu.mechanics.targets import MANIFEST_NAME

RunState = Literal[
    "initialized", "running", "partial", "completed", "interrupted", "failed"
]

#: Event → the state it puts the run in. An event with no entry here is a project
#: event and leaves the state alone, which is what makes the vocabulary open.
_STATE_EVENTS: dict[str, RunState] = {
    "initialized": "initialized",
    "started": "running",
    "resumed": "running",
    "finished": "completed",
    "incomplete": "partial",
    "interrupted": "interrupted",
    "error": "failed",
}

#: Suffixes on a ``step:<name>:<suffix>`` event that mean the step is satisfied.
#: ``completed`` is the ordinary case; ``not_applicable`` is a project declaring a
#: required step does not apply to this run, which is deliberately *not* the same
#: as never mentioning it — see :meth:`Manifest.satisfied_steps`.
_SATISFYING_SUFFIXES = ("completed", "not_applicable")

RESERVED_EVENTS = frozenset(_STATE_EVENTS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Manifest:
    """The single writer for one run directory's ``manifest.json``.

    Reserved lifecycle events belong to the framework; a project may append its
    own events and may not write ``state``, which is always derived from the last
    reserved event and so cannot drift from the timeline.
    """

    def __init__(self, run_dir: Path, data: dict[str, Any]) -> None:
        self._path = run_dir / MANIFEST_NAME
        self._data = data

    # ---- construction -----------------------------------------------------

    @classmethod
    def create(
        cls,
        run_dir: Path,
        *,
        command: Sequence[str],
        identities: Mapping[str, Any] | None = None,
    ) -> Manifest:
        manifest = cls(
            run_dir,
            {
                "command": list(command),
                "state": "initialized",
                "encoder_version": CANONICAL_ENCODER_VERSION,
                "identities": dict(identities or {}),
                "hashes": {},
                "artifacts": {},
                "progress": {},
                "best": None,
                "parent": None,
                "configs": [],
                "invocations": [],
                "timeline": [],
                "failure": None,
            },
        )
        manifest._append_event("initialized")
        return manifest

    @classmethod
    def load(cls, run_dir: Path) -> Manifest:
        path = run_dir / MANIFEST_NAME
        if not path.is_file():
            raise RefusalError(f"no {MANIFEST_NAME} in {run_dir}")
        data = read_json(path)
        if not isinstance(data, dict):
            raise RefusalError(f"{path} is not a manifest object")
        return cls(run_dir, data)

    # ---- reading ----------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def state(self) -> RunState:
        return self._data["state"]

    @property
    def data(self) -> Mapping[str, Any]:
        return self._data

    def _step_events(self, *suffixes: str) -> frozenset[str]:
        found = set()
        for entry in self._data["timeline"]:
            event = str(entry.get("event", ""))
            if not event.startswith("step:"):
                continue
            for suffix in suffixes:
                if event.endswith(f":{suffix}"):
                    found.add(event[len("step:") : -len(f":{suffix}")])
        return frozenset(found)

    def completed_steps(self) -> frozenset[str]:
        """Steps recorded complete, so resume knows what to skip.

        Spans every invocation, which is what makes resume work: a step completed
        two jobs ago is complete now, whether or not this process entered it.
        """
        return self._step_events("completed")

    def satisfied_steps(self) -> frozenset[str]:
        """Steps that do not hold the run open — completed, or declared inapplicable.

        Wider than :meth:`completed_steps` on purpose. Resume asks "what can I
        skip", and a step declared inapplicable to this run was never going to run;
        completion asks "is anything outstanding", and that step is not.
        """
        return self._step_events(*_SATISFYING_SUFFIXES)

    # ---- writing ----------------------------------------------------------

    def flush(self) -> None:
        atomic_write_json(self._path, self._data)

    def _current_invocation(self) -> int | None:
        invocations = self._data["invocations"]
        return invocations[-1]["index"] if invocations else None

    def _append_event(self, event: str, **extra: Any) -> None:
        entry: dict[str, Any] = {"event": event, "at": _now()}
        invocation = self._current_invocation()
        if invocation is not None:
            entry["invocation"] = invocation
        entry.update(extra)
        self._data["timeline"].append(entry)
        if event in _STATE_EVENTS:
            self._data["state"] = _STATE_EVENTS[event]
        self.flush()

    def record_lifecycle_event(self, event: str, **extra: Any) -> None:
        """Append a reserved event. Framework use; a project calls the wrappers."""
        if event not in RESERVED_EVENTS:
            raise RefusalError(f"{event!r} is not a reserved lifecycle event")
        self._append_event(event, **extra)

    def record_event(self, event: str, **extra: Any) -> None:
        """Append a project event. Reserved names are refused, the state is untouched."""
        if event in RESERVED_EVENTS:
            raise RefusalError(
                f"{event!r} is a reserved lifecycle event; projects append their own "
                "event names and never write the state"
            )
        self._append_event(event, **extra)

    def record_invocation(
        self,
        *,
        command: Sequence[str],
        argv: str,
        config_index: int,
        config_hash: str | None = None,
        **extra: Any,
    ) -> int:
        """Log one invocation against this run. Returns its index.

        A run directory accumulates invocations because augmenting commands are
        invocations — evaluating a finished run, re-rendering figures. Each names
        the configuration index it ran under, so an invocation with overrides is
        distinguishable from one without.
        """
        index = len(self._data["invocations"])
        self._data["invocations"].append(
            {
                "index": index,
                "command": list(command),
                "argv": argv,
                "at": _now(),
                "config": config_index,
                "config_hash": config_hash,
                **extra,
            }
        )
        self.flush()
        return index

    def config_index_for(self, digest: str) -> tuple[int, bool]:
        """The index for a resolved configuration, allocating one if it is new.

        Returns ``(index, is_new)``. Keyed on the canonical bytes of the *resolved*
        configuration rather than on ``config_hash``: two invocations can differ
        only in a hash-excluded field — a different leaf template, say — and keying
        on the digest would silently collapse them into one record.

        Indices start at 1, and index 1 lives at the unsuffixed path, so the
        common single-configuration run keeps ``config/resolved.yaml`` where anyone
        would look for it.
        """
        for entry in self._data["configs"]:
            if entry["digest"] == digest:
                return entry["index"], False
        index = len(self._data["configs"]) + 1
        self._data["configs"].append({"index": index, "digest": digest})
        self.flush()
        return index, True

    def record_identity(self, name: str, value: Any) -> None:
        """Record an identity the project determined during work.

        Identity fields are read off the resolved configuration when the run opens;
        this is for one a project only learns later. Honbu never infers the set —
        not every hash warrants an identity, and not every identity comes from a
        hash.
        """
        self._data["identities"][name] = value
        self.flush()

    def record_hash(self, name: str, value: str, **extra: Any) -> None:
        """Record a computed identity, including one deferred until mid-run."""
        self._data["hashes"][name] = {"value": value, **extra}
        self.flush()

    def record_artifact(self, name: str, path: str | Path) -> None:
        """Name something a downstream operation consumes.

        An entry here is a *promise*, not an inventory. A backend's own scratch
        directory, a staggered intermediate CSV, per-epoch figures nothing reads —
        none of them belong, unless something else looks them up by this name.
        Overwrites, because the index points at current truth: a sweep wants the
        checkpoint, not every checkpoint.
        """
        self._data["artifacts"][name] = str(path)
        self.flush()

    def record_progress(self, **fields: Any) -> None:
        self._data["progress"] = {**fields, "updated": _now()}
        self.flush()

    def record_best(
        self,
        *,
        metrics: Mapping[str, Any],
        epoch: int | None = None,
        step: int | None = None,
        **extra: Any,
    ) -> None:
        """Store the project's chosen best result verbatim.

        Honbu neither selects it nor reads the metric names. This mapping is what
        comparison ranks on later; the project's own metric substrate — files, tall
        parquet rows — stays authoritative and untouched.
        """
        self._data["best"] = {
            "epoch": epoch,
            "step": step,
            "metrics": dict(metrics),
            **extra,
        }
        self.flush()

    def record_parent(self, *, run_dir: str | Path, config_hash: str, artifact: str) -> None:
        """Record that this run continues another.

        Continuing a finished run makes a *new* run: a directory whose artifacts
        came from one configuration for 100 epochs and another for 50 more has no
        honest configuration identity. Whether the child restores training state or
        only loads weights shows up in the child's own configuration; Honbu records
        the link and nothing about its meaning.
        """
        self._data["parent"] = {
            "run_dir": str(run_dir),
            "config_hash": config_hash,
            "artifact": artifact,
        }
        self.flush()

    def record_failure(self, *, kind: str, message: str, interrupted: bool) -> None:
        """End the run. ``interrupted`` means an external signal, not an exception.

        Resumability is not the discriminator — an OOM crash is often resumable
        too — so the split is on who stopped it.
        """
        self._data["failure"] = {"kind": kind, "message": message}
        self._append_event("interrupted" if interrupted else "error")

    def record_outcome(self, *, outstanding: Sequence[str] = ()) -> None:
        """Close a producing invocation that returned without raising.

        Returning is not by itself completion. A run whose required steps are all
        satisfied is ``completed``; one that returned with steps outstanding is
        ``partial``, which stays resumable — so running part of a stepped command,
        deliberately or by a branch nobody meant to take, does not masquerade as a
        finished run.
        """
        if outstanding:
            self._append_event("incomplete", outstanding=list(outstanding))
        else:
            self._append_event("finished")

    def record_completion(self) -> None:
        """Close a run as complete unconditionally. Prefer :meth:`record_outcome`."""
        self._append_event("finished")
