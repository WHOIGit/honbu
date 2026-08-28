"""The run: the live half of the lifecycle, and the only thing that writes.

A :class:`~honbu.flow.resolve.Resolution` is pure. Once its target is prepared,
:func:`open_run` builds a :class:`Run` around it, and from then on every write goes
through that one object: the provenance bundle, the manifest, the artifacts, the
hashes deferred until their inputs existed. One writer per mutable artifact, held
structurally rather than by convention.

The bundle is written in three waves, because a single atomic persist is not
achievable:

* before work — the composed configuration, the resolved configuration, and the
  hashes that resolved
* during work — each deferred hash as its inputs materialize, which for a dataset
  scan is minutes in
* at completion — the manifest's final state

Work receives the run, reads ``run.config``, and returns. Returning is completion;
raising is failure; a signal is an interruption. There is no return value, because
the best epoch's metrics are recorded live and *are* the comparison substrate.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from honbu.errors import RefusalError
from honbu.flow.commands import CommandSpec
from honbu.flow.manifest import Manifest
from honbu.flow.resolve import Resolution, anchor
from honbu.mechanics.blocks import OutputBlock, OutputLeaf
from honbu.mechanics.canonical import canonical_bytes, hash_source
from honbu.mechanics.hashes import (
    HashEntry,
    HashRegistry,
    write_hash_entries,
)
from honbu.mechanics.io import atomic_write_text
from honbu.mechanics.targets import (
    RESUMABLE_STATES,
    clear_contents,
    prepare_scope,
    prepare_target,
    read_existing_manifest,
    validate_destructive_target,
)

CONFIG_DIRNAME = "config"
COMPOSED_NAME = "composed.yaml"
RESOLVED_NAME = "resolved.yaml"


def _dump_yaml(payload: Mapping[str, Any]) -> str:
    """Serialize a configuration for human reading.

    Deliberately not the canonical encoder: that one exists to make bytes
    comparable, this one to make a file readable. ``sort_keys=False`` keeps blocks
    in schema order rather than alphabetized.
    """
    return yaml.safe_dump(dict(payload), sort_keys=False, default_flow_style=False)


def config_dir_for(run_dir: Path, index: int) -> Path:
    """``config/`` for the first configuration, ``config/<N>/`` after that."""
    base = run_dir / CONFIG_DIRNAME
    return base if index == 1 else base / str(index)


def resolved_digest(resolution: Resolution) -> str:
    """The dedup key for a configuration index: the portable view's canonical bytes."""
    return hash_source(resolution.portable.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


@dataclass
class Run:
    """A prepared run directory plus the single writer for everything in it."""

    resolution: Resolution
    manifest: Manifest
    run_dir: Path
    config_dir: Path
    hashes: HashRegistry
    invocation: int
    resuming: bool = False
    clobber: bool = False
    _active_step: str | None = field(default=None, repr=False)
    _completed: frozenset[str] = field(default_factory=frozenset, repr=False)

    # ---- reading ----------------------------------------------------------

    @property
    def config(self) -> Any:
        """The absolute view: every output path joined and directly usable."""
        return self.resolution.config

    @property
    def command(self) -> CommandSpec:
        return self.resolution.command

    @property
    def deferred(self) -> tuple[str, ...]:
        """Declared hashes still awaiting their inputs."""
        return tuple(
            name
            for name in self.resolution.deferred
            if name not in self.manifest.data["hashes"]
        )

    @property
    def progress(self) -> Mapping[str, Any]:
        """What the last invocation recorded, so a resumed step knows where it was.

        Honbu does not interpret it: the project chose the keys, and only the
        project knows whether ``epoch`` means "completed" or "in flight".
        """
        return dict(self.manifest.data.get("progress") or {})

    @property
    def best(self) -> Mapping[str, Any] | None:
        return self.manifest.data.get("best")

    def artifact(self, name: str) -> Path | None:
        """A registered artifact as an absolute path, or None if never registered.

        Stored relative and handed back usable, the same asymmetry as the two
        configuration views: the record survives the tree moving, the caller gets
        something it can open.
        """
        stored = self.manifest.data["artifacts"].get(name)
        if stored is None:
            return None
        candidate = Path(stored)
        return candidate if candidate.is_absolute() else self.run_dir / candidate

    def skip(self, step: str) -> bool:
        """Whether a resumed run has already completed this step.

        The project checks it, because a context manager cannot decline to run its
        own body::

            for name in ("prepare", "fit", "evaluate"):
                if run.skip(name):
                    continue
                with run.step(name):
                    ...
        """
        self.command.step(step)  # refuse an undeclared name even when skipping
        return self.resuming and step in self._completed

    # ---- steps ------------------------------------------------------------

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Enter a declared step: record its boundaries, prepare its scope.

        "Scope" governs what Honbu *clears* for this step and what it records —
        not what the project is able to write. There is no filesystem sandbox here,
        and pretending otherwise would be worse than saying so.

        On resume, whether this step's own prior output survives is the step's
        declared ``on_resume`` policy. ``continue`` (the default) leaves it, which
        is what lets a training step pick up from ``last.pt`` at epoch 10 rather
        than starting over. ``restart`` clears the declared scope, with no further
        override — the run is already recorded as not having completed this step,
        and demanding ``--clobber`` on every resume buys nothing.

        Nesting is refused so that "which steps completed" stays a set membership
        test rather than a tree walk, which is what resume needs.
        """
        spec = self.command.step(name)
        if self._active_step is not None:
            raise RefusalError(
                f"step {name!r} cannot nest inside {self._active_step!r}"
            )

        if self.resuming and spec.on_resume == "restart" and name not in self._completed:
            # Containment-checked, like every other path the framework clears. This
            # was the one destructive path that went straight to clear_contents:
            # `prepare_target` and `prepare_scope` both validate, so a step scope
            # templated outside the containment root was the only way to get the
            # framework to delete something the configuration never authorized.
            containment = _containment_root(self.resolution)
            for leaf in self._scope_paths(spec.scope):
                resolved = validate_destructive_target(leaf, containment_root=containment)
                if resolved.is_dir():
                    clear_contents(resolved)

        self._active_step = name
        self.manifest.record_event(f"step:{name}:started")
        try:
            yield
        except BaseException:
            self.manifest.record_event(f"step:{name}:failed")
            raise
        else:
            self.manifest.record_event(f"step:{name}:completed")
        finally:
            self._active_step = None

    def _scope_paths(self, scope: Sequence[str]) -> tuple[Path, ...]:
        return tuple(_scope_dirs(self.resolution, scope))

    # ---- writing ----------------------------------------------------------

    def complete_hash(
        self,
        name: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        provenance: str | None = None,
    ) -> dict[str, HashEntry]:
        """Compute a deferred hash now that its inputs exist, and persist it.

        This is the second wave of the bundle. The artifacts land beside the ones
        written before work, and the manifest records each value, so a run
        interrupted after the scan still carries the identity it established.

        Returns a mapping keyed by entry id, always — ``{"dataset_hash": …}`` for a
        plain declaration and ``{"head_hash.dorsal": …, "head_hash.caudal": …}`` for
        a fanned one. One shape rather than a union, so no caller has to type-check
        what it got back.
        """
        if name not in self.resolution.deferred:
            raise RefusalError(
                f"{name!r} was not deferred by this resolution; deferred: "
                f"{', '.join(self.resolution.deferred) or '(none)'}"
            )
        context: dict[str, Any] = {
            "config": self.config,
            "command": self.command.slug,
            **(inputs or {}),
        }
        result = self.hashes.compute(context, only=(name,), input_reading=True)
        if not result.entries:
            raise RefusalError(
                f"{name!r} still has no inputs; its declaration returned nothing"
            )
        entries = {
            entry_id: (
                entry if provenance is None else entry.model_copy(update={"provenance": provenance})
            )
            for entry_id, entry in result.entries.items()
        }
        write_hash_entries(self.config_dir, entries)
        for entry_id, entry in entries.items():
            self.manifest.record_hash(
                entry_id,
                entry.value,
                version=entry.version,
                provenance=entry.provenance,
            )
        return entries

    def record_artifact(self, name: str, path: str | Path) -> None:
        """Name something a downstream operation consumes, relative to the run.

        An absolute path inside the run directory is stored relative, so the index
        survives the tree being moved — the same reason the resolved configuration
        keeps its output paths relative.
        """
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(self.run_dir)
            except ValueError:
                pass
        self.manifest.record_artifact(name, candidate)

    def not_applicable(self, step: str, *, reason: str) -> None:
        """Declare a required step does not apply to this run, with a reason.

        Counts as satisfied, so the run can still complete — but it is *recorded*,
        which is the whole point. Permanently dropping such a step from the required
        set would make "correctly disabled this time" and "a branch nobody meant to
        take" both silent and identical; this way silence still means something is
        wrong, and deliberate omission carries its reason on the timeline.
        """
        self.command.step(step)  # refuse an undeclared name
        if step in self.manifest.completed_steps():
            raise RefusalError(
                f"step {step!r} already completed in this run; it cannot be declared "
                "not applicable"
            )
        self.manifest.record_event(f"step:{step}:not_applicable", reason=reason)

    def outstanding_steps(self) -> tuple[str, ...]:
        """Required steps neither completed nor declared inapplicable."""
        satisfied = self.manifest.satisfied_steps()
        return tuple(
            name for name in self.command.required_steps if name not in satisfied
        )

    def record_identity(self, name: str, value: Any) -> None:
        self.manifest.record_identity(name, value)

    def record_progress(self, **fields: Any) -> None:
        self.manifest.record_progress(**fields)

    def record_best(self, **fields: Any) -> None:
        self.manifest.record_best(**fields)

    def record_event(self, event: str, **extra: Any) -> None:
        self.manifest.record_event(event, **extra)


# --------------------------------------------------------------------------- #
# opening one
# --------------------------------------------------------------------------- #


def _scope_dirs(resolution: Resolution, scope: Sequence[str]) -> Iterator[Path]:
    """Absolute directories for declared scope paths.

    Reads the absolute view, so a leaf already joined to its block's directory
    comes back usable rather than as a bare relative name.
    """
    for dotted in scope:
        current: Any = resolution.config
        for segment in dotted.split("."):
            current = getattr(current, segment)
        if isinstance(current, OutputLeaf):
            if current.dir is not None:
                yield current.dir
        elif isinstance(current, OutputBlock):
            if current.dir is not None:
                yield current.dir
        elif isinstance(current, Path):
            yield current


def write_bundle(
    config_dir: Path,
    *,
    composed: Mapping[str, Any],
    resolution: Resolution,
) -> None:
    """The first wave: what was authored, what it resolved to, and the identities."""
    config_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(config_dir / COMPOSED_NAME, _dump_yaml(composed))
    atomic_write_text(
        config_dir / RESOLVED_NAME,
        _dump_yaml(resolution.portable.model_dump(mode="json")),
    )
    if resolution.hashes:
        write_hash_entries(config_dir, resolution.hashes)


def initialize_run(
    resolution: Resolution,
    *,
    composed: Mapping[str, Any],
    hashes: HashRegistry,
    argv_text: str,
    clobber: bool = False,
    parent: Mapping[str, Any] | None = None,
) -> Run:
    """Prepare the target and persist the bundle, without starting work.

    The run is left in ``initialized``, so a later invocation resumes it and runs
    the first step. Without this, ``initialized`` would exist only for the
    milliseconds between creating the manifest and recording ``started`` — and
    "set the run directory up now, train in tonight's allocation" would have no
    spelling.

    Returns the run rather than yielding it: nothing is running, so there is no
    completion to record and no exception to classify.
    """
    return _prepare(
        resolution,
        composed=composed,
        hashes=hashes,
        argv_text=argv_text,
        clobber=clobber,
        resume=False,
        parent=parent,
    )


@contextmanager
def open_run(
    resolution: Resolution,
    *,
    composed: Mapping[str, Any],
    hashes: HashRegistry,
    argv_text: str,
    clobber: bool = False,
    resume: bool = False,
    parent: Mapping[str, Any] | None = None,
) -> Iterator[Run]:
    """Prepare the target, persist the bundle, and yield the live run.

    On exit the manifest is closed out: returning is completion, a signal is an
    interruption, anything else raised is a failure. The distinction is *who
    stopped it*, not whether it can be resumed — an OOM crash often can be.

    A caveat worth stating: SIGTERM does not raise in Python by default, so a run
    killed by a scheduler stays ``running`` unless the project installs a handler.
    Honbu does not install one, because a framework that rewires signal handling
    out from under its host is worse than an occasionally stale state. ``running``
    is resumable for exactly this reason: the next allocation picks up where the
    last one was cut off.
    """
    run = _prepare(
        resolution,
        composed=composed,
        hashes=hashes,
        argv_text=argv_text,
        clobber=clobber,
        resume=resume,
        parent=parent,
    )
    manifest = run.manifest

    if not resolution.command.advances_run:
        # The invocation records itself and nothing about the run's state. Two
        # shapes share this branch: a command preparing a run for later work, which
        # leaves it initialized, and an augmenting command, which adds to a run
        # someone else produced. A failed evaluation has not un-trained a run, and
        # recording `failed` would destroy the fact that its artifacts are complete.
        manifest.record_event("invocation:started")
        try:
            yield run
        except BaseException as exc:
            manifest.record_event(
                "invocation:failed", kind=type(exc).__name__, message=str(exc)
            )
            raise
        else:
            manifest.record_event("invocation:finished")
        return

    manifest.record_lifecycle_event("resumed" if resume else "started")
    try:
        yield run
    except (KeyboardInterrupt, SystemExit) as exc:
        manifest.record_failure(
            kind=type(exc).__name__, message=str(exc) or "stopped", interrupted=True
        )
        raise
    except BaseException as exc:
        manifest.record_failure(
            kind=type(exc).__name__, message=str(exc), interrupted=False
        )
        raise
    else:
        manifest.record_outcome(outstanding=run.outstanding_steps())


def _prepare(
    resolution: Resolution,
    *,
    composed: Mapping[str, Any],
    hashes: HashRegistry,
    argv_text: str,
    clobber: bool,
    resume: bool,
    parent: Mapping[str, Any] | None,
) -> Run:
    """Everything both entry points do: target, manifest, bundle, invocation."""
    command = resolution.command
    if resolution.target is None:
        raise RefusalError(
            f"{command.invocation} declares no target, so it has no run to open"
        )

    run_dir = resolution.target
    containment = _containment_root(resolution)
    warnings = list(resolution.warnings)

    if command.mode == "augment":
        warnings.extend(
            prepare_scope(
                run_dir,
                _scope_paths_for(resolution, command.scope),
                containment_root=containment,
                clobber=clobber,
            )
        )
        manifest = Manifest.load(run_dir)
    elif resume:
        existing = read_existing_manifest(run_dir)
        if existing is None:
            raise RefusalError(f"nothing to resume in {run_dir}")
        state = existing.get("state")
        if state not in RESUMABLE_STATES:
            raise RefusalError(
                f"cannot resume a {state} run in {run_dir}; start a fresh run from "
                f"{run_dir / CONFIG_DIRNAME / COMPOSED_NAME}"
            )
        manifest = Manifest.load(run_dir)
    else:
        run_dir, target_warnings = prepare_target(
            run_dir, containment_root=containment, clobber=clobber
        )
        warnings.extend(target_warnings)
        manifest = Manifest.create(
            run_dir, command=command.name, identities=resolution.identities
        )

    index, is_new = manifest.config_index_for(resolved_digest(resolution))
    config_dir = config_dir_for(run_dir, index)
    if is_new or not (config_dir / RESOLVED_NAME).is_file():
        write_bundle(config_dir, composed=composed, resolution=resolution)

    for name, entry in resolution.hashes.items():
        manifest.record_hash(
            name, entry.value, version=entry.version, provenance=entry.provenance
        )

    invocation = manifest.record_invocation(
        command=command.name,
        argv=argv_text,
        config_index=index,
        config_hash=resolution.digests.get("config_hash"),
    )
    if parent is not None:
        manifest.record_parent(**dict(parent))

    return Run(
        resolution=resolution,
        manifest=manifest,
        run_dir=run_dir,
        config_dir=config_dir,
        hashes=hashes,
        invocation=invocation,
        resuming=resume,
        clobber=clobber,
        _completed=manifest.completed_steps(),
    )


def _containment_root(resolution: Resolution) -> Path:
    spec = resolution.command.target
    assert spec is not None  # open_run refused a targetless command already
    current: Any = resolution.config
    for segment in spec.root.split("."):
        current = getattr(current, segment)
    return Path(current).resolve()


def _scope_paths_for(resolution: Resolution, scope: Sequence[str]) -> tuple[Path, ...]:
    root = _containment_root(resolution)
    return tuple(anchor(path, base=root) for path in _scope_dirs(resolution, scope))


def bundle_bytes(resolution: Resolution) -> bytes:
    """Exposed for a project asserting that two resolutions are the same run."""
    return canonical_bytes(resolution.portable.model_dump(mode="json"))
