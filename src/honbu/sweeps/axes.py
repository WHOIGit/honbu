"""Interpret a sweep declaration as axes and child coordinates."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from honbu.core.markers import traversable_submodel
from honbu.errors import RefusalError
from honbu.sweeps.definitions import Axis, AxisKind, AxisOption, Coordinate, SweepBlock


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

