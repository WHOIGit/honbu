"""Preparation: expand a sweep, resolve every child, write them all, run nothing.

Preparation is deliberately a separate act from execution. Everything that can be
wrong about a sweep — a bad path, a gate that gates nothing, two children sharing a
directory — is wrong before any work starts, and finding out at child seven means
seven trainings that have to be thrown away. So the matrix is expanded, every child
resolved, the gate checked across all of them, and only then is anything written.

What is written is ordinary. A child is a run directory in the ``initialized``
state: a manifest, a configuration bundle, resolved hashes, and nothing else. It is
reachable by exactly the same call as any other run — identify the directory, ask
to resume — which is why execution can be a ``for`` loop and why a scheduler array
over the child directories needs no glue.

Re-preparation is idempotent. An existing child is left exactly as it is, including
a completed one, because preparing a sweep again is what you do after adding an
axis and the last thing you want is for it to clear the eleven children that
already ran.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.capabilities.gate import check_gate, require_gate
from honbu.capabilities.sweep import (
    SWEEP_BLOCK,
    Coordinate,
    SweepBlock,
    SweepPlan,
    plan_sweep,
    without_sweep_block,
)
from honbu.errors import RefusalError
from honbu.flow.commands import CommandSpec
from honbu.flow.resolve import Resolution, resolve
from honbu.flow.run import CONFIG_DIRNAME, initialize_run
from honbu.mechanics.hashes import HashRegistry
from honbu.mechanics.identifiers import deterministic_id
from honbu.mechanics.io import atomic_write_json
from honbu.mechanics.markers import parse_dotted
from honbu.mechanics.targets import MANIFEST_NAME
from honbu.mechanics.templates import TemplateContext

#: The child's own record of what produced it, beside its configuration bundle.
#: In the configuration directory rather than the manifest because it describes the
#: configuration — it is the desugared form of the axes, legible without
#: re-deriving the matrix from the sweep definition.
CHILD_COORDINATE_NAME = "sweep.json"

#: The sweep's immutable record of its children.
SWEEP_INDEX_NAME = "index.json"

#: The domain the sweep identity is seeded in, so a definition digest labelled for
#: another purpose cannot collide into the same name.
SWEEP_ID_DOMAIN = "sweep"

#: Composes one child, given the group selections it requires. Preparation supplies
#: nothing else: selections must reach the compositor, while ordinary assignments
#: are applied afterwards to the dict it returns.
ComposeChild = Callable[[Mapping[str, str]], dict[str, Any]]


def assign_path(node: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a dotted path in a composed tree, creating intermediates as needed.

    Creating intermediates is safe here only because axis-path validation has
    already run: every concrete container key on the path was confirmed present in
    the composed configuration, so nothing this creates is a fabricated entry. What
    it does create is the ordinary case of a leaf whose value came from a schema
    default and so was never written down.
    """
    current = node
    segments = parse_dotted(dotted, allow_index=True)
    for segment in segments[:-1]:
        nxt = current.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            current[segment] = nxt
        current = nxt
    current[segments[-1]] = value


def child_configuration(base: Mapping[str, Any], coordinate: Coordinate) -> dict[str, Any]:
    """One child's composed configuration: the base, with its axes applied.

    The sweep block is removed here, which is what makes a child's digests equal
    those of the same configuration invoked by hand. It is not the whole of that
    guarantee — the block is excluded from digests by its marker regardless (D28),
    since a base configuration may legitimately carry one during an ordinary run.
    """
    config = without_sweep_block(base)
    for path, value in coordinate.assignments.items():
        assign_path(config, path, deepcopy(value))
    return config


@dataclass(frozen=True)
class PreparedChild:
    """One child of a prepared sweep, and where it went."""

    index: int
    coordinate: Coordinate
    gate_value: Any
    run_dir: Path
    identities: Mapping[str, Any]
    #: False when the child already existed and was left untouched.
    created: bool

    def as_index_entry(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "gate": self.gate_value,
            "values": dict(self.coordinate.values),
            "run_dir": str(self.run_dir),
            "identities": dict(self.identities),
            "in_definition": True,
        }


@dataclass(frozen=True)
class Preparation:
    """The result of preparing a sweep: what exists, and what it is."""

    sweep_id: str
    sweep_hash: str
    gate: str
    sweep_dir: Path
    children: tuple[PreparedChild, ...]
    warnings: tuple[str, ...]
    #: Index entries from a previous definition that this one no longer covers.
    #: Kept, never deleted — a completed child's results stay reachable, and a sweep
    #: that once covered a value can say so.
    retired: tuple[Mapping[str, Any], ...] = ()

    def __len__(self) -> int:
        return len(self.children)

    @property
    def created(self) -> tuple[PreparedChild, ...]:
        return tuple(child for child in self.children if child.created)


def sweep_identity(sweep_dir: Path) -> str:
    """A human-readable identity for the sweep living in a directory.

    A sweep is anchored by its directory, exactly as a run is. That is what lets a
    sweep be *added to*: growing an axis extends the sweep in place rather than
    producing a second one, the same way an evaluation adds to a run instead of
    forking it.

    So the identity cannot come from the definition. An earlier version derived it
    from the definition digest, which made "the definition changed" and "this is a
    different sweep" the same statement — so adding one axis value renamed the sweep
    and, under a template naming it, relocated every child and orphaned whatever had
    already run. That digest still exists and still changes; it is the
    :func:`sweep_definition_digest`, which answers a different question.

    Derived from the path rather than freshly minted so that a first preparation
    needs no state to be reproducible, and persisted in the index so that a sweep
    keeps its identity if the directory is later moved.
    """
    return deterministic_id(SWEEP_ID_DOMAIN, str(Path(sweep_dir).resolve()))


def _resolve_child(
    config: Mapping[str, Any],
    *,
    sweep_id: str,
    root_type: type[BaseModel],
    command: CommandSpec,
    hashes: HashRegistry,
    context: TemplateContext,
    cwd: Path | None,
) -> Resolution:
    """Resolve one child, with its sweep membership available to the template.

    ``runtime.sweep_id`` is fed to the resolver but not persisted as authored: it is
    assigned, never written by hand, which is exactly what its resolved-only marker
    says. Supplying it here rather than after resolution is what lets a child output
    template name the sweep — ``{runtime.sweep_id}/{runtime.config_id}`` — so a
    sweep's children live together instead of scattering through the output root.

    It never reaches a digest: the marker excludes it, which is load-bearing, since
    otherwise a child's identity would incorporate its membership and could not be
    compared against the same configuration run alone.

    Naming the sweep in a child's template has a cost worth knowing before choosing
    it. A sweep's identity *is* its definition, so adding one axis value makes a
    different sweep — and every child then resolves to a new directory, orphaning
    whatever already ran. That is the one case re-preparation exists for, so a
    template naming the configuration alone (``{runtime.config_id}``) is usually the
    better default: growing the sweep then prepares exactly the children it gained.
    Nothing is lost but tidiness, because reporting locates children through the
    index rather than by where they sit.
    """
    seeded = deepcopy(dict(config))
    runtime = seeded.setdefault("runtime", {})
    if isinstance(runtime, dict):
        runtime["sweep_id"] = sweep_id
    return resolve(
        seeded,
        root_type=root_type,
        command=command,
        hashes=hashes,
        context=context,
        cwd=cwd,
    )


def prepare_sweep(
    composed: Mapping[str, Any],
    *,
    sweep_dir: Path,
    root_type: type[BaseModel],
    command: CommandSpec,
    hashes: HashRegistry,
    context: TemplateContext,
    compose_child: ComposeChild | None = None,
    compose_without: Any = None,
    overrides: Sequence[str] = (),
    cwd: Path | None = None,
    argv_text: str = "",
    block: SweepBlock | None = None,
) -> Preparation:
    """Expand, validate, resolve, check the gate, and write every child.

    Nothing is written until every check has passed over every child. That ordering
    is the whole point of preparation being its own act: a gate collision found
    after eight children exist is eight directories that have to be reasoned about,
    and a collision is exactly the failure that would otherwise be discovered when
    the ninth child overwrites the fourth.

    ``compose_child`` is how group axes reach the compositor, since a group
    selection carries a whole file and cannot be applied to a composed dict. A sweep
    with no group axes needs none.

    ``sweep_dir`` is the sweep, in the same sense that a run directory is the run.
    Preparing against a directory that already holds an index *extends* that sweep:
    it keeps its identity, keeps the children it has, and adds the ones the
    definition gained.
    """
    from honbu.capabilities.sweep import sweep_block_of

    block = block if block is not None else sweep_block_of(composed, root_type=root_type)
    if block is None:
        raise RefusalError(
            f"no active {SWEEP_BLOCK!r} block to prepare: declare at least one axis, "
            f"for example {SWEEP_BLOCK}.axes.epochs=[10,20]"
        )

    gate = require_gate(block)
    plan = plan_sweep(
        block,
        root_type=root_type,
        composed=composed,
        overrides=overrides,
        compose_without=compose_without,
    )

    # The identity comes from the directory, never from the definition, so that
    # growing an axis extends this sweep rather than naming a new one.
    existing = load_sweep_index(sweep_dir)
    sweep_id = (existing or {}).get("sweep_id") or sweep_identity(sweep_dir)

    # --- resolve every child before writing any of them ---------------------
    configs: list[dict[str, Any]] = []
    resolutions: list[Resolution] = []
    for coordinate in plan.coordinates:
        base = (
            compose_child(coordinate.selections)
            if coordinate.selections and compose_child is not None
            else dict(composed)
        )
        if coordinate.selections and compose_child is None:
            raise RefusalError(
                f"axis {sorted(coordinate.selections)} selects configuration-group "
                f"options, which must be applied during composition. Supply "
                f"compose_child to prepare a sweep with group axes."
            )
        config = child_configuration(base, coordinate)
        configs.append(config)
        resolutions.append(
            _resolve_child(
                config,
                sweep_id=sweep_id,
                root_type=root_type,
                command=command,
                hashes=hashes,
                context=context,
                cwd=cwd,
            )
        )

    def resolve_first(ctx: TemplateContext) -> Resolution:
        """The first child again, under a different invocation.

        One configuration is enough for the stability check: the gate is a path, not
        a value, so a path that is invocation-derived is invocation-derived for
        every child.
        """
        return _resolve_child(
            configs[0],
            sweep_id=sweep_id,
            root_type=root_type,
            command=command,
            hashes=hashes,
            context=ctx,
            cwd=cwd,
        )

    _check_gate_over(gate, plan, resolutions, command, resolve_first)

    # --- only now does anything land on disk --------------------------------
    children = tuple(
        _write_child(
            resolution,
            config=config,
            coordinate=coordinate,
            gate=gate,
            sweep_id=sweep_id,
            hashes=hashes,
            argv_text=argv_text,
        )
        for coordinate, config, resolution in zip(plan.coordinates, configs, resolutions)
    )

    retired = retired_entries((existing or {}).get("children", ()), children)
    write_sweep_index(
        sweep_dir,
        sweep_id=sweep_id,
        sweep_hash=plan.digest,
        gate=gate,
        children=children,
        retired=retired,
    )

    return Preparation(
        sweep_id=sweep_id,
        sweep_hash=plan.digest,
        gate=gate,
        sweep_dir=sweep_dir,
        children=children,
        warnings=plan.warnings,
        retired=retired,
    )


def _check_gate_over(
    gate: str,
    plan: SweepPlan,
    resolutions: Sequence[Resolution],
    command: CommandSpec,
    resolve_first: Callable[[TemplateContext], Resolution],
) -> None:
    """Every gate check, over the whole expansion, before anything is written."""
    from honbu.capabilities.gate import read_path

    if command.target is None:
        raise RefusalError(
            f"{command.invocation} declares no target, so it produces no run "
            f"directory and cannot be swept"
        )

    check_gate(
        gate,
        resolve_with=resolve_first,
        values=[read_path(r.portable, gate) for r in resolutions],
        coordinates=plan.coordinates,
        config=resolutions[0].portable,
        template_field=command.target.template,
        targets=[r.target for r in resolutions],
    )


def _write_child(
    resolution: Resolution,
    *,
    config: Mapping[str, Any],
    coordinate: Coordinate,
    gate: str,
    sweep_id: str,
    hashes: HashRegistry,
    argv_text: str,
) -> PreparedChild:
    """Write one child in the initialized state, or leave an existing one alone."""
    from honbu.capabilities.gate import read_path

    target = resolution.target
    assert target is not None  # guaranteed by the target check above
    gate_value = read_path(resolution.portable, gate)

    if (target / MANIFEST_NAME).is_file():
        return PreparedChild(
            index=coordinate.index,
            coordinate=coordinate,
            gate_value=gate_value,
            run_dir=target,
            identities=dict(resolution.identities),
            created=False,
        )

    run = initialize_run(
        resolution,
        composed=config,
        hashes=hashes,
        argv_text=argv_text,
    )
    run.manifest.record_event(
        "sweep:member",
        sweep_id=sweep_id,
        index=coordinate.index,
        values=dict(coordinate.values),
    )
    atomic_write_json(
        target / CONFIG_DIRNAME / CHILD_COORDINATE_NAME,
        {
            "sweep_id": sweep_id,
            "index": coordinate.index,
            "gate": gate,
            "gate_value": gate_value,
            "values": dict(coordinate.values),
        },
    )
    return PreparedChild(
        index=coordinate.index,
        coordinate=coordinate,
        gate_value=gate_value,
        run_dir=target,
        identities=dict(resolution.identities),
        created=True,
    )


def retired_entries(
    previous: Sequence[Mapping[str, Any]], children: Sequence[PreparedChild]
) -> tuple[dict[str, Any], ...]:
    """Index entries the current definition no longer covers.

    Kept rather than dropped. A child that ran and finished holds results, and an
    index that quietly stopped mentioning it would leave its directory on disk and
    unreferenced — the silent omission the index exists to prevent. So the entry
    survives, marked as outside the current definition, and reporting can say the
    sweep once covered that value.

    Keyed by gate value, because that is what a gate is for: the value that
    identifies a child. Keying by directory would lose the correspondence the moment
    an output template changed.
    """
    current = {repr(child.gate_value) for child in children}
    return tuple(
        {**dict(entry), "in_definition": False}
        for entry in previous
        if repr(entry.get("gate")) not in current
    )


def write_sweep_index(
    sweep_dir: Path,
    *,
    sweep_id: str,
    sweep_hash: str,
    gate: str,
    children: Sequence[PreparedChild],
    retired: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """The sweep's record of which children exist and where.

    Each entry is immutable once written — nothing here is a progress cache, and no
    child's state is stored. But the index as a whole *accumulates*, the way a run's
    manifest accumulates invocations: growing an axis adds entries and keeps the
    ones already there.

    ``sweep_hash`` is recorded and is allowed to change; it is what says the
    definition grew. ``sweep_id`` does not, because a sweep is its directory.

    Reporting locates children through this and never scans, so a child that was
    moved or deleted is reported missing rather than silently omitted from a total
    that then looks complete.
    """
    path = sweep_dir / CONFIG_DIRNAME / SWEEP_INDEX_NAME
    atomic_write_json(
        path,
        {
            "sweep_id": sweep_id,
            "sweep_hash": sweep_hash,
            "gate": gate,
            "children": [
                *(child.as_index_entry() for child in children),
                *(dict(entry) for entry in retired),
            ],
        },
    )
    return path


def read_sweep_index(sweep_dir: Path) -> dict[str, Any]:
    """The index, or a refusal naming what is missing."""
    from honbu.mechanics.io import read_json

    path = sweep_dir / CONFIG_DIRNAME / SWEEP_INDEX_NAME
    if not path.is_file():
        raise RefusalError(f"no sweep index at {path}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise RefusalError(f"{path} is not a sweep index")
    return data


def load_sweep_index(sweep_dir: Path) -> dict[str, Any] | None:
    """The index if the directory holds one, else None. Never refuses."""
    path = sweep_dir / CONFIG_DIRNAME / SWEEP_INDEX_NAME
    return read_sweep_index(sweep_dir) if path.is_file() else None
