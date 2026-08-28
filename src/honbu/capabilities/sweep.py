"""The sweep block, and the expansion of it into child coordinates.

A sweep is declared in exactly one place — a top-level ``sweep`` block — and never
on a field. A field cannot carry its own axis: one typed as an integer would have
to be retyped as a union to also hold a list of integers, which admits a sweep
specification into validation and into every digest; and on a genuinely
list-valued field "a list" and "a list of values to sweep" are indistinguishable.
So the block holds the whole declaration, and the schema stays a description of
one configuration.

The block has three parts. ``gate`` names the configuration path whose value
distinguishes children. ``axes`` maps a configuration path to the values it takes.
``covariant_axes`` maps a label to options that each assign *several* paths at
once — because a cartesian product cannot express co-variance: two parameters that
must move together describe a diagonal, and the diagonal of a grid is a different
object than the grid.

Every axis contributes exactly one dimension whatever its kind, so the child count
is the product of the declared lengths and never depends on how many *paths* an
axis happens to touch.

This module expands; it does not resolve, write, or run. Preparation is a separate
concern and takes what :func:`expand` returns.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from honbu.errors import RefusalError
from honbu.mechanics.canonical import hash_source
from honbu.mechanics.markers import (
    DIRECTIVE,
    DirectiveMarker,
    check_field_path,
    field_marker,
    excluded_source_paths,
    prune_path,
    traversable_submodel,
)
from honbu.mechanics.model import StrictModel

#: The one top-level block a sweep is declared in.
SWEEP_BLOCK = "sweep"

#: What an axis reaches, and when it is applied.
#:
#: ``path`` assigns a dotted configuration path after composition. ``group`` selects
#: a whole configuration-group option, and so must be applied *during* composition.
#: ``covariant`` assigns several paths at once, after composition.
AxisKind = Literal["path", "group", "covariant"]


class SweepBlock(StrictModel):
    """The sweep declaration.

    ``axes`` and ``covariant_axes`` are open mappings: their keys are configuration
    paths and labels the caller chooses, so no schema can enumerate them. That is
    what makes an override like ``sweep.axes.epochs=[10,20]`` work with no addition
    prefix and no dedicated option — see
    :func:`honbu.flow.compose.force_add_sweep_overrides` for the one accommodation
    the compositor's struct mode requires.

    ``axes`` is typed loosely because an override arrives *nested*: the compositor
    reads ``sweep.axes.model.backbone=[a,b]`` as a two-level mapping, while an
    authored file may spell the same axis as the flat dotted key
    ``model.backbone``. :func:`normalize_axes` reconciles the two.
    """

    gate: str | None = None
    axes: dict[str, Any] = Field(default_factory=dict)
    covariant_axes: dict[str, Any] = Field(default_factory=dict)

    def is_active(self) -> bool:
        """Whether this block declares anything to expand.

        A block present but empty is not a sweep. Projects default the field to an
        empty block so the path exists for an override to address, so "present" and
        "asked for" have to be different questions.
        """
        return bool(self.axes or self.covariant_axes)


@dataclass(frozen=True)
class AxisOption:
    """One point on one dimension.

    ``value`` is what gets recorded as the child's realized value for this axis —
    the scalar for a path axis, the option name for a group axis, the whole mapping
    for a covariant one. ``assignments`` and ``selections`` are how it is *applied*,
    and they are disjoint: a selection carries a whole file and must reach the
    compositor, an assignment addresses a path and is applied to the composed tree.
    """

    value: Any
    assignments: Mapping[str, Any] = field(default_factory=dict)
    selections: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Axis:
    """One dimension of the expansion.

    ``label`` is how the axis is named in messages and in a child's recorded
    coordinate: its configuration path for a path or group axis, its declared label
    for a covariant one. ``paths`` is every configuration path the axis claims,
    which is what the no-path-claimed-twice rule is checked over.
    """

    label: str
    kind: AxisKind
    paths: tuple[str, ...]
    options: tuple[AxisOption, ...]

    def __len__(self) -> int:
        return len(self.options)


@dataclass(frozen=True)
class Coordinate:
    """A child's position in the expansion.

    ``values`` is the legible form — axis label to realized value — and is what a
    child records so its membership is readable without re-deriving the matrix.
    ``assignments`` and ``selections`` are the applicable form, merged across every
    axis.
    """

    index: int
    values: Mapping[str, Any]
    assignments: Mapping[str, Any]
    selections: Mapping[str, str]


#: How a project declares the block on its root. The marker is not optional: it is
#: what keeps the block out of every digest, and so what makes a prepared child's
#: identity equal to that of the same configuration invoked by hand.
SweepDeclaration = Annotated[SweepBlock, DIRECTIVE]

#: The line a project adds to its root to gain sweeps. Quoted in refusals rather
#: than paraphrased, so what is reported is what compiles.
DECLARATION_LINE = f"{SWEEP_BLOCK}: SweepDeclaration = Field(default_factory=SweepBlock)"


def check_root_declares_sweep(root_type: type[BaseModel]) -> None:
    """Confirm the root carries a sweep field, and that it is marked.

    Sweeping is not universal, so the block is not a requirable one — a project that
    never sweeps declares nothing and pays nothing. The cost of that choice is that
    a project which *tries* to sweep without declaring it would otherwise be told
    only that extra inputs are not permitted, which names the rule rather than the
    fix. This says the fix.

    The marker is checked as well as the field, because forgetting it fails
    silently: everything works, and only the digests are wrong.
    """
    info = root_type.model_fields.get(SWEEP_BLOCK)
    if info is None:
        raise RefusalError(
            f"{root_type.__name__} declares no {SWEEP_BLOCK!r} block, so this project "
            f"cannot sweep. Add one line to the root:\n  {DECLARATION_LINE}"
        )
    if field_marker(info, DirectiveMarker) is None:
        raise RefusalError(
            f"{root_type.__name__}.{SWEEP_BLOCK} is declared but unmarked, so the "
            f"sweep definition would enter this project's digests and a prepared "
            f"child's identity would differ from the same configuration run by hand. "
            f"Declare it as:\n  {DECLARATION_LINE}"
        )


def sweep_block_of(
    composed: Mapping[str, Any], *, root_type: type[BaseModel] | None = None
) -> SweepBlock | None:
    """Read the sweep block out of a composed configuration, if it carries one.

    Returns None when the block is absent *or* present and empty, since both mean
    "this is not a sweep". A gate on its own is inert by that rule, deliberately: a
    project sets a default gate in its base configuration so that a sweep declared
    entirely on the command line inherits one, and if a gate alone counted as a
    sweep that default would break every ordinary run.

    A malformed block is a refusal rather than None: someone who wrote a ``sweep:``
    key meant something by it. Passing ``root_type`` additionally checks that the
    project declared the block properly, which is worth doing exactly here — at the
    first point a sweep is actually being asked for.
    """
    raw = composed.get(SWEEP_BLOCK)
    if raw is None:
        return None
    if root_type is not None:
        check_root_declares_sweep(root_type)
    if not isinstance(raw, Mapping):
        raise RefusalError(
            f"the {SWEEP_BLOCK!r} block must be a mapping, not {type(raw).__name__}"
        )
    try:
        block = SweepBlock.model_validate(dict(raw))
    except Exception as exc:
        raise RefusalError(f"invalid {SWEEP_BLOCK!r} block: {exc}") from None
    return block if block.is_active() else None


def without_sweep_block(composed: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a composed configuration with the sweep block removed.

    Preparation consumes the block, so a child never carries it. That is what makes
    a child's digest equal to the same configuration invoked by hand — otherwise
    every child's configuration identity would incorporate the entire sweep
    definition, and no child could be compared against a baseline.
    """
    from copy import deepcopy

    stripped = deepcopy(dict(composed))
    stripped.pop(SWEEP_BLOCK, None)
    return stripped


def normalize_axes(axes: Mapping[str, Any]) -> dict[str, list[Any]]:
    """Flatten an axis mapping to dotted path → values.

    Two spellings reach here and must land in the same place. An authored file
    naturally writes the flat dotted key::

        axes:
          model.backbone: [resnet50, convnext_tiny]

    while an override of the same axis arrives nested, because the compositor reads
    every dot in ``sweep.axes.model.backbone=[...]`` as a level::

        axes:
          model:
            backbone: [resnet50, convnext_tiny]

    The disambiguating rule is the value, not the key: an axis's values are a
    *sequence*, so a sequence terminates the descent and a mapping continues it.
    Nothing else could be meant — a mapping of values would be a covariant option,
    which is declared elsewhere.
    """
    flat: dict[str, list[Any]] = {}

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                walk(value, path)
                continue
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise RefusalError(
                    f"axis {path!r} must list its values; got "
                    f"{type(value).__name__}. A single value belongs in the base "
                    f"configuration, and a sweep of one is not a sweep."
                )
            if path in flat:
                raise RefusalError(f"axis {path!r} is declared twice")
            flat[path] = list(value)

    walk(axes, "")
    return flat


def _check_covariant_shape(label: str, options: Sequence[Mapping[str, Any]]) -> None:
    """Two of the three well-formedness rules, both about one covariant axis.

    A single option is a constant wearing the shape of an axis: it varies nothing,
    and the value it pins is invisible where anyone would look for it — so it
    belongs in the base configuration.

    Ragged options are worse, because they look right. An option omitting a path its
    siblings assign does not fail; it silently inherits the base configuration's
    value for that path, so one point of the diagonal is quietly not on it. Nothing
    downstream can recover the intent, since "inherit the base here" and "I forgot
    this one" are the same document.
    """
    if len(options) < 2:
        raise RefusalError(
            f"covariant axis {label!r} declares {len(options)} option(s); an axis "
            f"needs at least two. A single option is a constant — put it in the base "
            f"configuration, where it is visible."
        )
    expected = set(options[0])
    for position, option in enumerate(options[1:], start=1):
        missing = expected - set(option)
        extra = set(option) - expected
        if not missing and not extra:
            continue
        detail = []
        if missing:
            detail.append(f"omits {sorted(missing)}")
        if extra:
            detail.append(f"adds {sorted(extra)}")
        raise RefusalError(
            f"option {position} of covariant axis {label!r} {' and '.join(detail)} "
            f"relative to option 0. Every option must assign the same set of paths: "
            f"an omitted path silently inherits the base configuration instead of "
            f"varying, which is a hole wearing the shape of an axis."
        )


def normalize_covariant_axes(
    covariant_axes: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Check the shape of every covariant axis and flatten each option's keys.

    A covariant axis is label → list of options, each option a mapping of dotted
    path to value. Its options are *not* descended into the way ``axes`` are: the
    label is a name the author chose, not a path, so there is no nesting to
    reconcile at that level. Within an option the same flat-or-nested question
    arises for the assigned paths, and is answered the same way — except that here
    a mapping always continues the descent, because an option's leaves are single
    values rather than lists.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for label, options in covariant_axes.items():
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
            raise RefusalError(
                f"covariant axis {label!r} must list its options; got "
                f"{type(options).__name__}"
            )
        flattened: list[dict[str, Any]] = []
        for position, option in enumerate(options):
            if not isinstance(option, Mapping):
                raise RefusalError(
                    f"option {position} of covariant axis {label!r} must be a mapping "
                    f"of configuration path to value; got {type(option).__name__}"
                )
            flattened.append(_flatten_option(option, label, position))
        _check_covariant_shape(str(label), flattened)
        out[str(label)] = flattened
    return out


def _flatten_option(
    option: Mapping[str, Any], label: str, position: int
) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                walk(value, path)
                continue
            if path in flat:
                raise RefusalError(
                    f"option {position} of covariant axis {label!r} assigns "
                    f"{path!r} twice"
                )
            flat[path] = value

    walk(option, "")
    return flat


def axis_kind(root_type: type[BaseModel], path: str, values: Sequence[Any]) -> AxisKind:
    """Whether an axis selects configuration-group options or assigns a path.

    The distinction is not a declaration but a consequence of the schema. A
    configuration group is selected by name — ``data=nes-hf`` — and what it sets is
    a whole *block*. So an axis whose path names a model-typed field, listing
    strings, can only mean group selection: assigning the string ``nes-hf`` to a
    field typed as a data block would fail strict validation on every child, which
    is the same fact arriving later and worse.

    Everything else is a path axis. In particular an axis over a model-typed field
    whose values are *mappings* is a path assignment of whole sub-trees, not a group
    selection, and is left alone.

    The two are deliberately not interchangeable. A group file's package path is
    static — it is fixed by the file's location and ``@package`` header — so a group
    can never target a location whose key the *configuration* chose, such as a field
    inside a mapping keyed by a name. Anything below such a container is reachable
    only by a path or covariant axis.
    """
    if not all(isinstance(value, str) for value in values):
        return "path"
    current: Any = root_type
    for segment in path.split("."):
        fields = getattr(current, "model_fields", None)
        if not fields or segment not in fields:
            return "path"
        current = traversable_submodel(fields[segment].annotation)
        if current is None:
            return "path"
    return "group"


def sweep_axes(block: SweepBlock, *, root_type: type[BaseModel]) -> tuple[Axis, ...]:
    """The block's axes, normalized, classified, and in a stable order.

    Ordinary axes precede covariant ones, each group in declaration order, so the
    child index of a given sweep definition does not depend on mapping iteration
    luck.
    """
    axes: list[Axis] = []

    for path, values in normalize_axes(block.axes).items():
        kind = axis_kind(root_type, path, values)
        if kind == "group":
            options = tuple(
                AxisOption(value=value, selections={path: str(value)})
                for value in values
            )
        else:
            options = tuple(
                AxisOption(value=value, assignments={path: value}) for value in values
            )
        axes.append(Axis(label=path, kind=kind, paths=(path,), options=options))

    for label, options_raw in normalize_covariant_axes(block.covariant_axes).items():
        claimed: list[str] = []
        for option in options_raw:
            for path in option:
                if path not in claimed:
                    claimed.append(path)
        axes.append(
            Axis(
                label=label,
                kind="covariant",
                paths=tuple(claimed),
                options=tuple(
                    AxisOption(value=dict(option), assignments=dict(option))
                    for option in options_raw
                ),
            )
        )

    return tuple(axes)


def expand(axes: Sequence[Axis]) -> tuple[Coordinate, ...]:
    """The cartesian product of every dimension.

    One dimension per axis, whatever its kind — a covariant axis of two options
    contributes two, not one per path it assigns. Combined with an ordinary axis of
    three, that is six children, and every child's covarying paths come from a
    single option.
    """
    if not axes:
        return ()

    coordinates: list[Coordinate] = []
    for index, combination in enumerate(
        itertools.product(*(axis.options for axis in axes))
    ):
        values: dict[str, Any] = {}
        assignments: dict[str, Any] = {}
        selections: dict[str, str] = {}
        for axis, option in zip(axes, combination):
            values[axis.label] = option.value
            assignments.update(option.assignments)
            selections.update(option.selections)
        coordinates.append(
            Coordinate(
                index=index,
                values=values,
                assignments=assignments,
                selections=selections,
            )
        )
    return tuple(coordinates)


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
