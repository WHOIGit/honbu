"""Preparation: expand a sweep, resolve every child, write them all, run nothing.

Preparation is deliberately a separate act from execution. Everything that can be
wrong about a sweep — a bad path, a gate that gates nothing, two children sharing a
directory — is wrong before any work starts, and finding out at child seven means
seven trainings that have to be thrown away. So the matrix is expanded, every child
resolved (skipping cross-field ``@model_validator`` rejections while refusing
field-level typos), the gate checked across all of them, and only then is anything
written.

What is written is ordinary. A child is a run directory in the ``initialized``
state: a manifest, a configuration bundle, resolved hashes, and nothing else. It is
reachable by exactly the same call as any other run — identify the directory, ask
to resume — which is why execution can be a ``for`` loop and why a scheduler array
over the child directories needs no glue.

Re-preparation is idempotent and extensible in place (D29). An existing child is
left untouched, growing an axis adds only the new children under the same
``sweep_id``, and removing an axis value retires its child in the index without
deleting its directory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from honbu.core.hashes import HashRegistry
from honbu.core.identifiers import deterministic_id
from honbu.core.io import atomic_write_json, atomic_write_text
from honbu.core.markers import parse_dotted
from honbu.core.targets import MANIFEST_NAME
from honbu.core.templates import TemplateContext, TemplateRegistry, template_value_paths
from honbu.errors import DeclarationError, RefusalError
from honbu.flow.commands import CommandSpec, TargetSpec, field_default
from honbu.flow.compose import ComposeSpec
from honbu.flow.project import ProjectSpec
from honbu.flow.resolve import Resolution, resolve
from honbu.flow.run import (
    COMPOSED_NAME,
    CONFIG_DIRNAME,
    RESOLVED_NAME,
    initialize_run,
)
from honbu.sweeps.definitions import (
    SWEEP_BLOCK,
    Coordinate,
    SweepBlock,
    without_sweep_block,
)
from honbu.sweeps.gate import check_gate, require_gate
from honbu.sweeps.plan import SweepPlan, plan_sweep, sweep_composers

#: The child's own record of what produced it, beside its configuration bundle.
CHILD_COORDINATE_NAME = "sweep.json"

#: The sweep's immutable record of its children.
SWEEP_INDEX_NAME = "index.json"

#: The domain the sweep identity is seeded in, so a path labelled for another
#: purpose cannot collide into the same name.
SWEEP_ID_DOMAIN = "sweep"

#: Composes one child, given the group selections it requires.
ComposeChild = Callable[[Mapping[str, str]], dict[str, Any]]


def _dump_yaml(payload: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(payload), sort_keys=False, default_flow_style=False)


def assign_path(node: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a dotted path (supporting sequence indices) in a composed tree.

    Creating intermediate dicts is safe here only because axis-path validation has
    already run: every concrete container key or sequence index on the path was
    confirmed present in the composed configuration.
    """
    current: Any = node
    segments = parse_dotted(dotted, allow_index=True)
    for segment in segments[:-1]:
        if isinstance(current, list):
            current = current[int(segment)]
        else:
            nxt = current.get(segment)
            if not isinstance(nxt, (dict, list)):
                nxt = {}
                current[segment] = nxt
            current = nxt
    last = segments[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def child_configuration(base: Mapping[str, Any], coordinate: Coordinate) -> dict[str, Any]:
    """One child's composed configuration: the base, with its axes applied."""
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
class SkippedChild:
    """A sweep combination rejected by a project cross-field model validator."""

    index: int
    values: Mapping[str, Any]
    reason: str

    def as_index_entry(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "values": dict(self.values),
            "reason": self.reason,
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
    retired: tuple[Mapping[str, Any], ...] = ()
    #: Combinations skipped by project cross-field ``@model_validator`` rejections.
    skipped: tuple[SkippedChild, ...] = ()
    #: Resolution of ``SweepOutputBlock`` when ``sweep_target`` was used.
    sweep_resolution: Resolution | None = None

    def __len__(self) -> int:
        return len(self.children)

    @property
    def created(self) -> tuple[PreparedChild, ...]:
        return tuple(child for child in self.children if child.created)


def sweep_identity(sweep_dir: Path) -> str:
    """A human-readable identity for the sweep living in a directory (D29).

    A sweep is anchored by its directory, never by its definition, so growing an
    axis extends the sweep in place rather than renaming it. Derived from the
    resolved path on first write and persisted in ``config/index.json`` so moving
    the directory later keeps its identity.
    """
    return deterministic_id(SWEEP_ID_DOMAIN, str(Path(sweep_dir).resolve()))


def _is_cross_field_validator_rejection(exc: ValidationError) -> bool:
    """Whether every error in ``exc`` came from a custom ``ValueError`` validator.

    Cross-field ``@model_validator`` rejections produce ``type == "value_error"``
    and represent valid-schema combinations a project rejects (skipped into
    ``Preparation.skipped``). Field-level type, literal, missing, or constraint
    typos produce distinct error types and are refused outright.
    """
    errors = exc.errors()
    return bool(errors) and all(err.get("type") == "value_error" for err in errors)


def _format_validator_reason(exc: ValidationError) -> str:
    messages = [str(err.get("msg", "")) for err in exc.errors()]
    return "; ".join(m for m in messages if m) or str(exc)


def _check_sweep_template_not_swept(
    composed: Mapping[str, Any],
    *,
    root_type: type[BaseModel],
    sweep_target: TargetSpec,
    plan: SweepPlan,
) -> None:
    """Refuse a sweep output template referencing any path varied by a sweep axis."""
    current: Any = composed
    for segment in sweep_target.template.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        else:
            current = None
            break
    template = current if isinstance(current, str) else field_default(root_type, sweep_target.template)
    if not isinstance(template, str):
        return
    swept_paths = {path for axis in plan.axes for path in axis.paths}
    for ref in template_value_paths(template):
        for swept in swept_paths:
            if ref == swept or ref.startswith(f"{swept}.") or swept.startswith(f"{ref}."):
                raise RefusalError(
                    f"sweep output template {template!r} references swept axis path "
                    f"{ref!r} (swept by {swept!r}); a sweep directory must not vary per child"
                )


def _resolve_child(
    config: Mapping[str, Any],
    *,
    sweep_id: str,
    root_type: type[BaseModel],
    command: CommandSpec,
    hashes: HashRegistry,
    context: TemplateContext,
    templates: TemplateRegistry | None,
    cwd: Path | None,
) -> Resolution:
    """Resolve one child with its sweep membership supplied explicitly.

    ``sweep_id`` is passed via ``resolve(..., sweep_id=...)`` rather than
    authored into ``config`` so ``runtime.sweep_id`` stays strictly resolved-only
    while remaining available to child output templates such as
    ``{runtime.sweep_id}/{runtime.config_id}``. Because ``sweep_id`` derives from
    the sweep directory (D29), growing an axis keeps ``sweep_id`` stable and
    re-preparation adds only newly introduced children.
    """
    return resolve(
        config,
        root_type=root_type,
        command=command,
        hashes=hashes,
        context=context,
        templates=templates,
        cwd=cwd,
        sweep_id=sweep_id,
    )


def prepare_sweep(
    composed: Mapping[str, Any],
    *,
    command: CommandSpec,
    sweep_dir: Path | None = None,
    sweep_target: TargetSpec | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    context: TemplateContext | None = None,
    templates: TemplateRegistry | None = None,
    compose_spec: ComposeSpec | None = None,
    extra_dirs: Sequence[Path | str] = (),
    compose_child: ComposeChild | None = None,
    compose_without: Any = None,
    overrides: Sequence[str] = (),
    cwd: Path | None = None,
    argv_text: str = "",
    block: SweepBlock | None = None,
    project: ProjectSpec | None = None,
    config_hash_name: str | None = None,
) -> Preparation:
    """Expand, validate, resolve, check the gate, and write every child.

    Supply either an explicit ``sweep_dir`` (persisting ``config/composed.yaml``
    and ``config/index.json``) or ``sweep_target`` pointing at an opt-in
    :class:`~honbu.sweeps.SweepOutputBlock` (resolving the sweep directory and
    its reporting leaves, and persisting ``config/composed.yaml``,
    ``config/resolved.yaml``, and ``config/index.json``).

    When ``compose_spec`` (or ``project``) is given, group-axis child composition
    and group-shadow validation are wired automatically via
    :func:`~honbu.sweeps.sweep_composers`; explicit ``compose_child`` /
    ``compose_without`` callbacks remain available as escape hatches.
    """
    from honbu.sweeps.definitions import sweep_block_of

    if sweep_dir is None and sweep_target is None:
        raise RefusalError(
            "prepare_sweep() requires either an explicit sweep_dir or a sweep_target"
        )

    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    if resolved_root is None:
        raise DeclarationError("prepare_sweep() requires root_type or project")
    resolved_hashes = (
        hashes
        if hashes is not None
        else (project.hashes if project is not None else HashRegistry([]))
    )
    resolved_templates = (
        templates
        if templates is not None
        else (project.templates if project is not None else None)
    )
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    ctx = context if context is not None else TemplateContext.now()

    child_cb = compose_child
    without_cb = compose_without
    if spec is not None and (child_cb is None or without_cb is None):
        built_child, built_without = sweep_composers(
            spec, overrides=overrides, extra_dirs=extra_dirs, cwd=cwd
        )
        if child_cb is None:
            child_cb = built_child
        if without_cb is None:
            without_cb = built_without

    block = block if block is not None else sweep_block_of(composed, root_type=resolved_root)
    if block is None:
        raise RefusalError(
            f"no active {SWEEP_BLOCK!r} block to prepare: declare at least one axis, "
            f"for example {SWEEP_BLOCK}.axes.epochs=[10,20]"
        )

    gate = require_gate(block)
    plan = plan_sweep(
        block,
        root_type=resolved_root,
        composed=composed,
        overrides=overrides,
        compose_without=without_cb,
    )

    warnings: list[str] = list(plan.warnings)
    sweep_resolution: Resolution | None = None

    if sweep_dir is None:
        assert sweep_target is not None
        _check_sweep_template_not_swept(
            composed, root_type=resolved_root, sweep_target=sweep_target, plan=plan
        )
        sweep_cmd = CommandSpec(
            name=("sweep",),
            target=sweep_target,
            hashes=command.hashes,
            advances_run=False,
        )
        sweep_resolution = resolve(
            composed,
            root_type=resolved_root,
            command=sweep_cmd,
            hashes=resolved_hashes,
            context=ctx,
            templates=resolved_templates,
            cwd=cwd,
            allow_sweep_block=True,
        )
        assert sweep_resolution.target is not None
        target_sweep_dir = sweep_resolution.target
        warnings.extend(sweep_resolution.warnings)
    else:
        target_sweep_dir = Path(sweep_dir)

    # The identity comes from the directory, never from the definition, so that
    # growing an axis extends this sweep rather than naming a new one (D29).
    existing = load_sweep_index(target_sweep_dir)
    sweep_id = (existing or {}).get("sweep_id") or sweep_identity(target_sweep_dir)

    # --- resolve every child before writing any of them ---------------------
    valid_coordinates: list[Coordinate] = []
    valid_configs: list[dict[str, Any]] = []
    valid_resolutions: list[Resolution] = []
    skipped: list[SkippedChild] = []

    for coordinate in plan.coordinates:
        if coordinate.selections and child_cb is None:
            raise RefusalError(
                f"axis {sorted(coordinate.selections)} selects configuration-group "
                f"options, which must be applied during composition. Supply "
                f"compose_spec (or compose_child) to prepare a sweep with group axes."
            )
        base = child_cb(coordinate.selections) if coordinate.selections and child_cb is not None else dict(composed)
        config = child_configuration(base, coordinate)
        try:
            resolution = _resolve_child(
                config,
                sweep_id=sweep_id,
                root_type=resolved_root,
                command=command,
                hashes=resolved_hashes,
                context=ctx,
                templates=resolved_templates,
                cwd=cwd,
            )
        except ValidationError as exc:
            if _is_cross_field_validator_rejection(exc):
                reason = _format_validator_reason(exc)
                skipped.append(
                    SkippedChild(
                        index=coordinate.index,
                        values=dict(coordinate.values),
                        reason=reason,
                    )
                )
                warnings.append(
                    f"skipped child #{coordinate.index} {dict(coordinate.values)}: {reason}"
                )
                continue
            raise RefusalError(
                f"child #{coordinate.index} {dict(coordinate.values)} failed schema "
                f"validation: {exc}"
            ) from None

        valid_coordinates.append(coordinate)
        valid_configs.append(config)
        valid_resolutions.append(resolution)
        warnings.extend(resolution.warnings)

    if not valid_resolutions:
        raise RefusalError(
            "all expanded sweep combinations were rejected by model validation; "
            "zero valid children remain"
        )

    def resolve_first(divergent_ctx: TemplateContext) -> Resolution:
        return _resolve_child(
            valid_configs[0],
            sweep_id=sweep_id,
            root_type=resolved_root,
            command=command,
            hashes=resolved_hashes,
            context=divergent_ctx,
            templates=resolved_templates,
            cwd=cwd,
        )

    _check_gate_over(
        gate, valid_coordinates, valid_resolutions, command, resolve_first
    )

    # --- only now does anything land on disk --------------------------------
    children = tuple(
        _write_child(
            resolution,
            config=config,
            coordinate=coordinate,
            gate=gate,
            sweep_id=sweep_id,
            hashes=resolved_hashes,
            argv_text=argv_text,
            config_hash_name=config_hash_name,
            project=project,
        )
        for coordinate, config, resolution in zip(
            valid_coordinates, valid_configs, valid_resolutions
        )
    )

    retired = retired_entries((existing or {}).get("children", ()), children)
    sweep_cfg_dir = target_sweep_dir / CONFIG_DIRNAME
    sweep_cfg_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(sweep_cfg_dir / COMPOSED_NAME, _dump_yaml(composed))
    if sweep_resolution is not None:
        atomic_write_text(
            sweep_cfg_dir / RESOLVED_NAME,
            _dump_yaml(sweep_resolution.portable.model_dump(mode="json")),
        )
    write_sweep_index(
        target_sweep_dir,
        sweep_id=sweep_id,
        sweep_hash=plan.digest,
        gate=gate,
        children=children,
        retired=retired,
        skipped=skipped,
    )

    return Preparation(
        sweep_id=sweep_id,
        sweep_hash=plan.digest,
        gate=gate,
        sweep_dir=target_sweep_dir,
        children=children,
        warnings=tuple(dict.fromkeys(warnings)),
        retired=retired,
        skipped=tuple(skipped),
        sweep_resolution=sweep_resolution,
    )


def _check_gate_over(
    gate: str,
    coordinates: Sequence[Coordinate],
    resolutions: Sequence[Resolution],
    command: CommandSpec,
    resolve_first: Callable[[TemplateContext], Resolution],
) -> None:
    """Every gate check, over the whole expansion, before anything is written."""
    from honbu.sweeps.gate import read_path

    if command.target is None:
        raise RefusalError(
            f"{command.invocation} declares no target, so it produces no run "
            f"directory and cannot be swept"
        )

    check_gate(
        gate,
        resolve_with=resolve_first,
        values=[read_path(r.portable, gate) for r in resolutions],
        coordinates=coordinates,
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
    config_hash_name: str | None = None,
    project: ProjectSpec | None = None,
) -> PreparedChild:
    """Write one child in the initialized state, or leave an existing one alone."""
    from honbu.sweeps.gate import read_path

    target = resolution.target
    assert target is not None
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
        config_hash_name=config_hash_name,
        project=project,
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
    """Index entries the current definition no longer covers."""
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
    skipped: Sequence[SkippedChild] = (),
) -> Path:
    """Persist ``config/index.json``: active children, retired entries, and skipped combos."""
    path = sweep_dir / CONFIG_DIRNAME / SWEEP_INDEX_NAME
    payload: dict[str, Any] = {
        "sweep_id": sweep_id,
        "sweep_hash": sweep_hash,
        "gate": gate,
        "children": [
            *(child.as_index_entry() for child in children),
            *(dict(entry) for entry in retired),
        ],
    }
    if skipped:
        payload["skipped"] = [item.as_index_entry() for item in skipped]
    atomic_write_json(path, payload)
    return path


def read_sweep_index(sweep_dir: Path) -> dict[str, Any]:
    """The index, or a refusal naming what is missing."""
    from honbu.core.io import read_json

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
