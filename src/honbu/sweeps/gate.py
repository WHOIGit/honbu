"""The gate: the value a sweep uses to tell its children apart.

A sweep needs one answer to "which child is this?" that is not the child's index,
because an index is a fact about the expansion rather than about the
configuration — reorder the axes and every index moves. So a sweep declares a
configuration path, and the value at that path identifies the child.

Three things have to be true of it, and each is checked because each fails
silently otherwise.

It must be **distinct** across children, or two children are one run.

It must depend only on the **configuration** and not on the invocation. A value
minted fresh per invocation — a timestamp, a random identity — can never collide,
so it passes the distinctness check while gating nothing at all. This is the
failure worth designing against: it looks like the strongest possible gate right
up until two identical configurations are silently treated as different
experiments.

It must appear in the child **output template**. Otherwise the semantic check and
the filesystem can disagree: children could be distinct by the gate and still
resolve to one directory, so the check that passed and the thing it was protecting
would be about different questions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from honbu.errors import RefusalError
from honbu.core.markers import parse_dotted
from honbu.core.templates import TemplateContext, template_value_paths

#: A resolver bound to one configuration, parameterized only by the invocation
#: context. Preparation supplies it, so this module needs to know nothing about
#: composition.
ResolveWith = Callable[[TemplateContext], Any]


def read_path(config: BaseModel, dotted: str) -> Any:
    """The value at a dotted path of a resolved configuration.

    Refuses rather than returning None for a path that does not exist, because a
    gate naming a missing field would otherwise read as None for every child —
    which is a collision, reported as one, with the actual mistake nowhere in the
    message.
    """
    current: Any = config
    walked: list[str] = []
    for segment in parse_dotted(dotted):
        if not hasattr(current, segment):
            where = ".".join(walked) or type(config).__name__
            raise RefusalError(
                f"gate {dotted!r} names {segment!r}, which {where} does not have"
            )
        walked.append(segment)
        current = getattr(current, segment)
    return current


def divergent_contexts() -> tuple[TemplateContext, TemplateContext]:
    """Two invocation contexts sharing nothing an invocation is free to vary.

    Different clocks and different entropy, which between them reach every token in
    the invocation namespace. A value that survives both is a function of the
    configuration; one that does not was never a gate.

    The clocks differ by more than a day and land in different years, so a value is
    not accidentally stable because a template only rendered a year or a month.
    """
    return (
        TemplateContext(
            now_local=datetime(2020, 1, 2, 3, 4, 5),
            now_utc=datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            seed="0" * 16,
        ),
        TemplateContext(
            now_local=datetime(2031, 11, 27, 22, 58, 59),
            now_utc=datetime(2031, 11, 27, 22, 58, 59, tzinfo=timezone.utc)
            + timedelta(hours=7),
            seed="f" * 16,
        ),
    )


def check_gate_is_stable(gate: str, *, resolve_with: ResolveWith) -> Any:
    """Resolve one configuration twice and require both the gate and target to agree.

    Verified rather than trusted, because the alternative is a rule in a document.
    The two obvious ways to declare a useless gate — pointing it at ``run_id``, or
    at any template carrying a timestamp — are both things a careful person writes
    on the way to something else, and neither announces itself: the sweep prepares,
    every child gets its own directory, and the gate has silently been an alias for
    the child index. Likewise, a stable gate paired with a child output template
    carrying ``{runtime.run_id}`` or ``{:timestamp}`` would relocate every child
    on re-preparation and orphan completed runs.

    One configuration is enough. The gate and output template are paths, not
    values, so an invocation-derived path is invocation-derived for every child.
    """
    first, second = divergent_contexts()
    r1 = resolve_with(first)
    r2 = resolve_with(second)
    here = read_path(r1.portable, gate)
    there = read_path(r2.portable, gate)
    if here != there:
        raise RefusalError(
            f"gate {gate!r} is not stable: resolving one configuration under two "
            f"invocations gave {here!r} and {there!r}. A value minted per invocation "
            f"can never collide, so it would gate nothing while appearing to pass. "
            f"Point the gate at a configuration-derived value — a configuration digest "
            f"or an identity templated from one."
        )
    t1 = getattr(r1, "target", None)
    t2 = getattr(r2, "target", None)
    if t1 != t2:
        raise RefusalError(
            f"child output target is not stable across invocations "
            f"({t1!r} vs {t2!r}), even though gate {gate!r} is stable. "
            f"Remove per-invocation tokens (such as {{runtime.run_id}} or "
            f"{{:timestamp}}) from the child output template so re-preparation "
            f"does not orphan existing children."
        )
    return here


def check_gate_in_output_template(
    gate: str, *, config: BaseModel, template_field: str
) -> None:
    """Require the gate to appear in the template that names a child's directory.

    Without this the two protections disagree about what they protect. Distinctness
    is a claim about *values*; children overwriting one another is a fact about
    *directories*. Tying the gate to the template makes distinct gates imply
    distinct directories, so one check covers both.
    """
    template = read_path(config, template_field)
    if template is None:
        raise RefusalError(
            f"a swept command must name its children by template, but "
            f"{template_field!r} is not set. A literal directory would put every "
            f"child in one place."
        )
    if gate in template_value_paths(str(template)):
        return
    raise RefusalError(
        f"gate {gate!r} does not appear in the child output template "
        f"{str(template)!r}. Children distinct by the gate could still resolve to "
        f"one directory, so the gate would not protect what it is checked against. "
        f"Add {{{gate}}} to the template."
    )


@dataclass(frozen=True)
class Collision:
    """One gate value claimed by more than one child."""

    value: Any
    indices: tuple[int, ...]
    #: Axes whose realized values differ across the colliding children — precisely
    #: the ones the gate failed to reflect.
    undistinguished: tuple[str, ...]


def find_collisions(
    values: Sequence[Any], coordinates: Sequence[Any]
) -> tuple[Collision, ...]:
    """Group children by gate value and describe every group of more than one."""
    grouped: dict[str, list[int]] = {}
    for index, value in enumerate(values):
        grouped.setdefault(repr(value), []).append(index)

    collisions = []
    for indices in grouped.values():
        if len(indices) < 2:
            continue
        undistinguished = [
            label
            for label in coordinates[indices[0]].values
            if len({repr(coordinates[i].values[label]) for i in indices}) > 1
        ]
        collisions.append(
            Collision(
                value=values[indices[0]],
                indices=tuple(indices),
                undistinguished=tuple(undistinguished),
            )
        )
    return tuple(collisions)


def check_gate_distinguishes(
    gate: str, values: Sequence[Any], coordinates: Sequence[Any]
) -> None:
    """Refuse when two children share a gate value.

    Checked across every child before any directory is written, so a collision is a
    setup error rather than something discovered when the ninth child overwrites the
    fourth — by which point the fourth's work is gone and its manifest describes a
    configuration that is no longer there.
    """
    collisions = find_collisions(values, coordinates)
    if not collisions:
        return

    lines = [
        f"gate {gate!r} does not distinguish every child: "
        f"{len(collisions)} value(s) are claimed by more than one."
    ]
    for collision in collisions[:5]:
        members = ", ".join(
            f"#{index} {dict(coordinates[index].values)}" for index in collision.indices
        )
        lines.append(f"  {collision.value!r} <- {members}")
        if collision.undistinguished:
            lines.append(
                f"    varies by {list(collision.undistinguished)}, which the gate "
                f"does not reflect"
            )
    if len(collisions) > 5:
        lines.append(f"  ... and {len(collisions) - 5} more")
    lines.append(
        "Either widen what the gate reads (for example the hash or templated "
        "field backing it) so it covers those axes, or choose a gate path that "
        "varies across every colliding axis."
    )
    raise RefusalError("\n".join(lines))


def check_targets_are_distinct(
    targets: Sequence[Any], coordinates: Sequence[Any]
) -> None:
    """Require every child to resolve to its own directory.

    Not redundant with the gate being distinct and appearing in the template, and
    the gap between them is narrow enough to be worth naming. The template rule
    asks whether the gate's *path* is read; it cannot ask what the reading renders
    to, and a template function may be lossy. ``{experiment.name:slug}`` maps
    ``reef v3`` and ``reef-v3`` to one segment, so two children hold distinct gate
    values, satisfy both earlier checks, and still land in one directory.

    So the semantic claim and the physical one are checked separately: distinct
    gate values mean "these are different experiments", distinct directories mean
    "neither will overwrite the other". The template rule is what usually makes the
    second follow from the first; this is what happens when it does not.
    """
    grouped: dict[str, list[int]] = {}
    for index, target in enumerate(targets):
        grouped.setdefault(str(target), []).append(index)

    collisions = {
        target: indices for target, indices in grouped.items() if len(indices) > 1
    }
    if not collisions:
        return

    lines = [
        f"{len(collisions)} output director(ies) are claimed by more than one child, "
        f"so children would overwrite one another:"
    ]
    for target, indices in list(collisions.items())[:5]:
        members = ", ".join(
            f"#{index} {dict(coordinates[index].values)}" for index in indices
        )
        lines.append(f"  {target} <- {members}")
    if len(collisions) > 5:
        lines.append(f"  ... and {len(collisions) - 5} more")
    lines.append(
        "The gate distinguishes these children but the output template does not "
        "preserve the distinction — most often because a template function collapses "
        "it. Render the gate without a lossy function, or add the child index."
    )
    raise RefusalError("\n".join(lines))


def check_gate(
    gate: str,
    *,
    resolve_with: ResolveWith,
    values: Sequence[Any],
    coordinates: Sequence[Any],
    config: BaseModel,
    template_field: str,
    targets: Sequence[Any] | None = None,
) -> None:
    """Every gate check, in the order whose failures are cheapest to fix.

    Stability first: an unstable gate makes the collision report meaningless, since
    fresh values never collide and the sweep would look fine. Then the template,
    which is one edit. Distinctness last, because fixing it may mean rethinking the
    axes — and the directory check after it, since it is the rarer, narrower case
    and its message only makes sense once the gate itself is known to be sound.
    """
    check_gate_is_stable(gate, resolve_with=resolve_with)
    check_gate_in_output_template(gate, config=config, template_field=template_field)
    check_gate_distinguishes(gate, values, coordinates)
    if targets is not None:
        check_targets_are_distinct(targets, coordinates)


def require_gate(block: Any) -> str:
    """The declared gate, or a refusal explaining what it is for."""
    gate = getattr(block, "gate", None)
    if gate:
        return str(gate)
    raise RefusalError(
        "this sweep declares no gate. A sweep needs one configuration path whose "
        "value identifies a child, so that two children cannot silently become one "
        "run. Set it once in the base configuration — for example "
        "sweep.gate=runtime.config_id — and every sweep inherits it."
    )
