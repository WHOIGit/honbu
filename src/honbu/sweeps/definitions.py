"""The sweep declaration and immutable value objects.

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

This module declares values only; planning, preparation, and execution are separate
concerns.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from honbu.errors import RefusalError
from honbu.core.markers import (
    DIRECTIVE,
    DirectiveMarker,
    field_marker,
)
from honbu.core.model import StrictModel

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


def declared_block_type(root_type: type[BaseModel]) -> type[SweepBlock]:
    """The block type this project declared, which may add fields to Honbu's.

    The block is the one place a sweep is declared, and where a project's sweeps go
    or what to call this one are declarations of the same kind. Validating the raw
    mapping against :class:`SweepBlock` regardless would refuse those fields as extra
    inputs — making "the block is the single sweep definition" true of Honbu's half
    of it only.

    Strictness is not what is being relaxed. A subclass is strict too, so a
    misspelled key is still refused; it is refused against the schema that actually
    applies rather than against a stricter one the project did not write.
    """
    info = root_type.model_fields.get(SWEEP_BLOCK)
    declared = getattr(info, "annotation", None) if info is not None else None
    if isinstance(declared, type) and issubclass(declared, SweepBlock):
        return declared
    return SweepBlock


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
    first point a sweep is actually being asked for, and it is also what lets the
    block be read through the project's own type rather than through Honbu's.
    """
    raw = composed.get(SWEEP_BLOCK)
    if raw is None:
        return None
    block_type = SweepBlock
    if root_type is not None:
        check_root_declares_sweep(root_type)
        block_type = declared_block_type(root_type)
    if not isinstance(raw, Mapping):
        raise RefusalError(
            f"the {SWEEP_BLOCK!r} block must be a mapping, not {type(raw).__name__}"
        )
    try:
        block = block_type.model_validate(dict(raw))
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
    stripped = deepcopy(dict(composed))
    stripped.pop(SWEEP_BLOCK, None)
    return stripped

