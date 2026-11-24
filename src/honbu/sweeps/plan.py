"""Validate and finalize a sweep plan."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.core.canonical import hash_source
from honbu.core.markers import check_field_path, excluded_source_paths, prune_path
from honbu.errors import DeclarationError, RefusalError
from honbu.flow.compose import ComposeSpec, compose_config
from honbu.flow.project import ProjectSpec
from honbu.sweeps.axes import expand, normalize_axes, normalize_covariant_axes, sweep_axes
from honbu.sweeps.definitions import (
    SWEEP_BLOCK,
    Axis,
    Coordinate,
    SweepBlock,
    without_sweep_block,
)


def check_no_path_claimed_twice(axes: Sequence[Axis]) -> None:
    """The third well-formedness rule, and the only one that spans the block.

    Two claims on one path mean one silently wins per child, and which one is an
    artifact of application order rather than anything the author wrote. The rule
    holds across kinds, not just within them: an ordinary axis and a covariant axis
    claiming the same path is the same defect as two ordinary axes doing so, and is
    the easier one to write by accident.
    """
    claimant: dict[str, Axis] = {}
    for axis in axes:
        for path in axis.paths:
            prior = claimant.get(path)
            if prior is not None:
                raise RefusalError(
                    f"{path!r} is claimed by two axes: {prior.label!r} "
                    f"({prior.kind}) and {axis.label!r} ({axis.kind}). One would "
                    f"silently win per child; declare the path once."
                )
            claimant[path] = axis


def check_axis_paths(
    axes: Sequence[Axis],
    *,
    root_type: type[BaseModel],
    composed: Mapping[str, Any],
) -> None:
    """Both path checks, over every path every axis claims.

    A bad path is a refusal, never a skipped combination. Skipping is for a
    combination a project's own validator rejects — a real point of the matrix that
    turned out not to be valid. A bad path is not one bad point: it spoils every
    child, and recording that as skipping would report a sweep that merely found
    nothing, which is the wrong story about the wrong thing.
    """
    for axis in axes:
        for path in axis.paths:
            problem = check_field_path(
                root_type, path, data=composed, traverse_containers=True
            )
            if problem is None:
                continue
            check = "schema" if problem.kind == "unknown_field" else "configuration"
            raise RefusalError(
                f"axis {axis.label!r} targets {path!r}, which fails the {check} "
                f"check: {problem.message}"
            )


def override_key(token: str) -> str | None:
    """The dotted configuration path an override token addresses.

    Strips leading ``+``/``~`` prefixes, drops any ``@package`` relocation suffix,
    and normalizes Hydra slash-separated group paths (``model/head`` → ``model.head``).
    Returns None for a token that addresses nothing assignable.
    """
    body = token.lstrip("+~")
    if not body:
        return None
    key = body.split("=", 1)[0].strip()
    if not key:
        return None
    if "@" in key:
        key = key.split("@", 1)[0]
    key = key.strip("/").replace("/", ".")
    return key or None


def check_no_override_contests_an_axis(
    axes: Sequence[Axis], overrides: Sequence[str]
) -> None:
    """Refuse an override that assigns a path the sweep already sweeps.

    Such an override is a no-op — the axis assigns that path per child regardless,
    so whatever the caller typed is discarded — and a no-op shaped like an
    instruction is worse than an error, because it is indistinguishable from having
    worked.

    A *descendant* of a swept path is refused too, and for a group axis that is the
    substantive case: a group is merged rather than substituted, so
    ``data.split=val`` survives every selection and leaks into all children instead
    of being replaced by the chosen file.

    Overrides addressing the block itself redefine the sweep and are left alone.
    """
    claimed = {path: axis for axis in axes for path in axis.paths}
    for token in overrides:
        key = override_key(token)
        if key is None or key == SWEEP_BLOCK or key.startswith(f"{SWEEP_BLOCK}."):
            continue
        for path, axis in claimed.items():
            if key != path and not key.startswith(f"{path}."):
                continue
            form = (
                f"{SWEEP_BLOCK}.covariant_axes.{axis.label}=[...]"
                if axis.kind == "covariant"
                else f"{SWEEP_BLOCK}.axes.{path}=[...]"
            )
            raise RefusalError(
                f"override {token!r} assigns {key!r}, which axis {axis.label!r} "
                f"already sweeps. It would be discarded rather than applied. "
                f"Express it as an axis instead:\n  {form}"
            )


def axis_displacement_warnings(
    axes: Sequence[Axis], composed: Mapping[str, Any]
) -> tuple[str, ...]:
    """One warning per axis that displaces a value the base configuration sets."""
    warnings: list[str] = []
    for axis in axes:
        displaced = {}
        for path in axis.paths:
            present, value = _lookup(composed, path)
            if present:
                displaced[path] = value
        if not displaced:
            continue
        shown = ", ".join(f"{path}={value!r}" for path, value in displaced.items())
        warnings.append(
            f"axis {axis.label!r} overrides the composed configuration for every "
            f"child, displacing {shown}"
        )
    return tuple(warnings)


def _lookup(node: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Whether a dotted path (including sequence indices) is present, and its value."""
    current: Any = node
    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return False, None
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            if not segment.lstrip("-").isdigit():
                return False, None
            index = int(segment)
            if not -len(current) <= index < len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def sweep_composers(
    compose_spec: ComposeSpec,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    cwd: Path | None = None,
) -> tuple[Callable[[Mapping[str, str]], dict[str, Any]], Callable[[str], dict[str, Any]]]:
    """Build ``(compose_child, compose_without)`` callbacks for group axes (D24).

    Automatically forwards all non-sweep ``overrides`` and ``extra_dirs``, and
    translates nested dotted group paths to Hydra slash syntax
    (``model.head`` → ``model/head=<option>`` and ``~model/head``).
    """
    base_overrides = [
        token
        for token in overrides
        if (k := override_key(token)) is None
        or (k != SWEEP_BLOCK and not k.startswith(f"{SWEEP_BLOCK}."))
    ]

    def _group_syntax(dotted: str) -> str:
        return dotted.replace(".", "/")

    def compose_child(selections: Mapping[str, str]) -> dict[str, Any]:
        group_overrides = [
            f"{_group_syntax(group)}={option}"
            for group, option in selections.items()
        ]
        return compose_config(
            compose_spec,
            overrides=[*base_overrides, *group_overrides],
            extra_dirs=extra_dirs,
            cwd=cwd,
        )

    def compose_without(group: str) -> dict[str, Any]:
        return compose_config(
            compose_spec,
            overrides=[*base_overrides, f"~{_group_syntax(group)}"],
            extra_dirs=extra_dirs,
            cwd=cwd,
        )

    return compose_child, compose_without


def check_no_group_is_shadowed(
    axes: Sequence[Axis],
    *,
    compose_without: Any,
) -> None:
    """Refuse a group axis whose package path the base configuration also supplies."""
    for axis in axes:
        if axis.kind != "group":
            continue
        group = axis.paths[0]
        present, remainder = _lookup(compose_without(group), group)
        if not present or not remainder:
            continue
        keys = sorted(remainder) if isinstance(remainder, Mapping) else [group]
        raise RefusalError(
            f"the base configuration supplies {group!r} directly ({', '.join(keys)}) "
            f"as well as selecting it as a group, and axis {axis.label!r} sweeps it. "
            f"A group is merged rather than substituted, so every child would get the "
            f"union of the base and the selected option rather than the option. Move "
            f"those keys into the group's option files."
        )


@dataclass(frozen=True)
class SweepPlan:
    """A validated sweep, expanded but not yet resolved or written."""

    axes: tuple[Axis, ...]
    coordinates: tuple[Coordinate, ...]
    warnings: tuple[str, ...]
    digest: str

    def __len__(self) -> int:
        return len(self.coordinates)


def plan_sweep(
    block: SweepBlock,
    *,
    composed: Mapping[str, Any],
    root_type: type[BaseModel] | None = None,
    overrides: Sequence[str] = (),
    compose_spec: ComposeSpec | None = None,
    extra_dirs: Sequence[Path | str] = (),
    compose_without: Any = None,
    project: ProjectSpec | None = None,
    cwd: Path | None = None,
) -> SweepPlan:
    """Validate a sweep block and expand it. Refuses; never partially succeeds.

    When ``compose_spec`` (or ``project``) is supplied and ``compose_without`` is
    omitted, group-shadow composition is wired automatically via
    :func:`sweep_composers`. Explicit ``compose_without`` stays available as an
    escape hatch.
    """
    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    if resolved_root is None:
        raise DeclarationError("plan_sweep() requires root_type or project")
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    without_cb = compose_without
    if without_cb is None and spec is not None:
        _, without_cb = sweep_composers(
            spec, overrides=overrides, extra_dirs=extra_dirs, cwd=cwd
        )

    axes = sweep_axes(block, root_type=resolved_root)
    check_no_path_claimed_twice(axes)
    check_axis_paths(axes, root_type=resolved_root, composed=composed)
    check_no_override_contests_an_axis(axes, overrides)
    if without_cb is not None:
        check_no_group_is_shadowed(axes, compose_without=without_cb)
    return SweepPlan(
        axes=axes,
        coordinates=expand(axes),
        warnings=axis_displacement_warnings(axes, composed),
        digest=sweep_definition_digest(composed, root_type=resolved_root, block=block),
    )


def sweep_definition_digest(
    composed: Mapping[str, Any], *, root_type: type[BaseModel], block: SweepBlock
) -> str:
    """A digest over what a sweep *is*: its base configuration and its axes."""
    base = without_sweep_block(composed)
    for path in excluded_source_paths(root_type):
        prune_path(base, path)
    return hash_source(
        {
            "base": base,
            "axes": normalize_axes(block.axes),
            "covariant_axes": normalize_covariant_axes(block.covariant_axes),
        }
    )
