"""Validate and finalize a sweep plan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from honbu.core.canonical import hash_source
from honbu.core.markers import check_field_path, excluded_source_paths, prune_path
from honbu.errors import RefusalError
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
    """The configuration path an override token addresses, prefixes stripped.

    Returns None for a token that addresses nothing assignable.
    """
    body = token.lstrip("+~")
    if not body:
        return None
    key = body.split("=", 1)[0].strip()
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
    """One warning per axis that displaces a value the base configuration sets.

    The axis wins; this says so. There is deliberately no policy and no flag. A
    stricter rule — refuse until the base stops setting the path — fails three ways:
    it is incompatible with group axes, whose package path the defaults list
    *always* sets; the misspelling it chiefly guarded against is caught precisely by
    axis-path validation; and its remedy pressures an author to delete a default
    that is meaningful when the configuration runs alone.

    Once per axis rather than once per child, so a sweep of two hundred children
    does not bury it under two hundred copies.
    """
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
    """Whether a dotted path is present in a composed tree, and its value."""
    current: Any = node
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def check_no_group_is_shadowed(
    axes: Sequence[Axis],
    *,
    compose_without: Any,
) -> None:
    """Refuse a group axis whose package path the base configuration also supplies.

    A configuration group is *merged*, not substituted. If the base sets
    ``data.split`` directly and the sweep selects between ``data`` options, every
    child gets the union — the selected file plus the base's leftovers — so an
    ablation meant to swap the block wholesale silently fails to swap part of it.
    The failure is invisible in the result: every child looks like it composed.

    Detection needs composition rather than the block alone, because after
    composition the package path holds the merged product and carries no record of
    where each key came from. ``compose_without`` is called with a group name and
    must return the configuration composed with that group dropped from the defaults
    list; whatever remains at the package path is, exactly, what the base supplies.
    """
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
    """A validated sweep, expanded but not yet resolved or written.

    Everything that can be checked without composing a child has been. What remains
    for preparation is per-child work: resolving each combination, checking the gate
    across them, and writing the directories.
    """

    axes: tuple[Axis, ...]
    coordinates: tuple[Coordinate, ...]
    warnings: tuple[str, ...]
    digest: str

    def __len__(self) -> int:
        return len(self.coordinates)


def plan_sweep(
    block: SweepBlock,
    *,
    root_type: type[BaseModel],
    composed: Mapping[str, Any],
    overrides: Sequence[str] = (),
    compose_without: Any = None,
) -> SweepPlan:
    """Validate a sweep block and expand it. Refuses; never partially succeeds.

    The order is chosen so the first thing reported is the thing most likely to be
    wrong and cheapest to fix. Shape before paths, because a ragged covariant axis
    makes every subsequent message about a path that was never coherently claimed.
    Paths before expansion, because a bad path spoils every child and there is no
    point building a matrix out of it.

    ``compose_without`` is optional so a sweep can be planned against a
    configuration dict alone. Without it the group-shadow check cannot run, since it
    is the only check that needs composition; a caller that has a compositor should
    pass one.
    """
    axes = sweep_axes(block, root_type=root_type)
    check_no_path_claimed_twice(axes)
    check_axis_paths(axes, root_type=root_type, composed=composed)
    check_no_override_contests_an_axis(axes, overrides)
    if compose_without is not None:
        check_no_group_is_shadowed(axes, compose_without=compose_without)
    return SweepPlan(
        axes=axes,
        coordinates=expand(axes),
        warnings=axis_displacement_warnings(axes, composed),
        digest=sweep_definition_digest(composed, root_type=root_type, block=block),
    )


def sweep_definition_digest(
    composed: Mapping[str, Any], *, root_type: type[BaseModel], block: SweepBlock
) -> str:
    """A digest over what a sweep *is*: its base configuration and its axes.

    Under the same exclusions as any other behavioral source, so generated
    identities and output locations stay out of it. Two sweeps differing only in
    where they write therefore share a definition digest, which is what makes the
    digest usable as a claim about the experiment rather than about the filesystem.

    The gate is excluded deliberately: it names how children are *told apart*, not
    what they are. Renaming the gate does not change the set of configurations the
    sweep describes.
    """
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
