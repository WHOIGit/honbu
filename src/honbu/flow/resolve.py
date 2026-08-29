"""The resolver: a composed dict in, a pure :class:`Resolution` out.

Eight phases in a fixed order Honbu owns (D11). The order is the documentation —
printable, with one answer to "what happened when", which matters because the
resolved configuration is an artifact someone reads at 2am asking why a digest
moved. A dependency graph would turn a wrong declaration into a wrong order with
no error; a lazy pull model would give no linear narrative at all.

    1 validate composed -> RootConfig
    2 resolve input paths to absolute
    3 compute config-only hashes for the active command
    4 assign digests into their declared fields
    5 render template-valued fields
    6 render the command's target template
    7 resolve the target's output leaves
    8 revalidate

It holds for every case in the sibling projects, including the one that nearly
breaks it: identities derived from identities. Every id derives from a *hash*,
never from another id, so splitting 3 from 4 leaves phase 4 order-free internally.
Phases 2 and 3 are independent because paths never enter a digest — if they did,
absolutizing one would make digests machine-dependent.

Nothing here writes. Write methods live on the run object the lifecycle builds
around a Resolution once its target is prepared, which is also what makes read-only
inspection honest: it gets a Resolution, and no write is in reach.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.errors import RefusalError
from honbu.flow.commands import CommandSpec
from honbu.core.blocks import OutputBlock, OutputLeaf, output_block_fields
from honbu.core.hashes import HashEntry, HashRegistry
from honbu.core.markers import (
    WILDCARD,
    PathMarker,
    ResolvedOnlyMarker,
    TemplatedMarker,
    identity_bindings,
    identity_field_names,
    marked_field_paths,
    prune_path,
)
from honbu.core.paths import is_cwd_relative, path_length_refusal
from honbu.core.templates import (
    TemplateContext,
    TemplateRegistry,
    render_template,
    template_value_paths,
)


@dataclass(frozen=True)
class Resolution:
    """One resolved configuration, in both views, plus what resolution learned.

    ``config`` has every output path joined and absolute — it is what work reads,
    and the obvious name is deliberately the usable one. ``portable`` is what gets
    persisted: bare-relative output paths stay relative so a runs tree survives
    being moved. The two are the same configuration, and their digests are equal,
    because paths never enter a digest.
    """

    config: BaseModel
    portable: BaseModel
    command: CommandSpec
    context: TemplateContext
    hashes: Mapping[str, HashEntry] = field(default_factory=dict)
    deferred: tuple[str, ...] = ()
    target: Path | None = None
    warnings: tuple[str, ...] = ()

    @property
    def digests(self) -> dict[str, str]:
        """Every computed hash by entry id. Not identities — see :meth:`identities`."""
        return {name: entry.value for name, entry in self.hashes.items()}

    @property
    def identities(self) -> dict[str, Any]:
        """Values of the fields declared as run identities.

        Deliberately not the hash digests, which an earlier draft conflated with
        this. Not every hash warrants an identity, and not every identity derives
        from a hash — a fresh ``run_id`` derives from nothing — so the set comes
        from declarations rather than from what happened to be computed.
        """
        out: dict[str, Any] = {}
        for name, path in identity_field_names(type(self.portable)):
            if WILDCARD in path:
                continue
            value: Any = self.portable
            for segment in path:
                value = getattr(value, segment, None)
                if value is None:
                    break
            if value is not None:
                out[name] = value
        return out


# --------------------------------------------------------------------------- #
# path helpers
# --------------------------------------------------------------------------- #


def strip_resolved_only(
    composed: Mapping[str, Any], root_type: type[BaseModel]
) -> dict[str, Any]:
    """Drop every resolved-only field from a composed tree.

    This is what separates the two ways of reusing a prior run's configuration.
    Replaying it keeps everything, so its literal output directory wins and the
    invocation lands in that same run. *Continuing* from it must not: the child is a
    new run with its own identity, and inheriting the parent's directory, run id and
    digests would put two configurations' artifacts in one place with no honest
    configuration identity for either.

    So continuing strips the run-specific values and re-resolves, which is the
    resolved-only marker finally doing the job it was declared for.
    """
    from copy import deepcopy

    stripped = deepcopy(dict(composed))
    for path in marked_field_paths(root_type, ResolvedOnlyMarker):
        prune_path(stripped, path)
    return stripped


def _authored(composed: Mapping[str, Any], dotted: str) -> bool:
    """Whether the composed configuration supplied this path at all.

    The both-set refusal has to ask this rather than looking at the validated
    model, because a schema default legitimately provides ``dir_template`` while
    the caller pins ``dir``. Only *authoring* both is ambiguous.
    """
    current: Any = composed
    for segment in dotted.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current[segment]
    return current is not None


def _authored_value(composed: Mapping[str, Any], dotted: str) -> Any:
    current: Any = composed
    for segment in dotted.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def anchor(value: Path, *, base: Path) -> Path:
    """Join a stored output path to its anchor. Absolute values pass through."""
    return value if value.is_absolute() else (base / value)


def _iter_leaves(block: OutputBlock) -> list[tuple[str, OutputLeaf]]:
    return [
        (name, value)
        for name, value in vars(block).items()
        if isinstance(value, OutputLeaf)
    ]


def _get(model: BaseModel, dotted: str) -> Any:
    current: Any = model
    for segment in dotted.split("."):
        current = getattr(current, segment)
    return current


def _set(model: BaseModel, dotted: str, value: Any) -> None:
    segments = dotted.split(".")
    current: Any = model
    for segment in segments[:-1]:
        current = getattr(current, segment)
    setattr(current, segments[-1], value)


def _set_path(model: BaseModel, path: Sequence[str], value: Any) -> None:
    current: Any = model
    for segment in path[:-1]:
        if segment == "*":  # a container step; identity fields inside one are
            return  # addressed by the project, not assigned here
        current = getattr(current, segment)
    setattr(current, path[-1], value)


# --------------------------------------------------------------------------- #
# the phases
# --------------------------------------------------------------------------- #


def _phase2_input_paths(config: BaseModel, *, cwd: Path, skip: frozenset[str]) -> None:
    """Absolutize authored input paths. Output paths are handled in 6 and 7."""
    for path in marked_field_paths(type(config), PathMarker):
        dotted = ".".join(path)
        if "*" in path or dotted in skip or dotted.split(".")[0] in skip:
            continue
        try:
            value = _get(config, dotted)
        except AttributeError:
            continue
        if isinstance(value, Path) and not value.is_absolute():
            _set(config, dotted, (cwd / value).resolve())


def _phase4_identities(
    config: BaseModel,
    entries: Mapping[str, HashEntry],
    deferred: Sequence[str] = (),
) -> tuple[str, ...]:
    """Copy each computed digest into the field that declared it.

    Returns the dotted paths left unassigned *because their hash was deferred* —
    the set a target template may not reference.

    Deliberately not every unassigned path. A field naming a hash nobody declared
    is also unassigned, and reporting it as deferred would be a misleading message
    for a different mistake: that one is refused at import by
    ``validate_identity_fields``, with the null-token refusal as the backstop if a
    caller drives the resolver directly with a mismatched registry.
    """
    postponed = set(deferred)
    unassigned: list[str] = []
    for path, hash_name in identity_bindings(type(config)):
        entry = entries.get(hash_name)
        if entry is None:
            if hash_name in postponed:
                unassigned.append(".".join(path))
            continue
        _set_path(config, path, entry.value)
    return tuple(unassigned)


def _phase5_templated(
    config: BaseModel,
    *,
    context: TemplateContext,
    registry: TemplateRegistry,
) -> tuple[str, ...]:
    """Render every template-valued field in place.

    Before the target renders, because a run directory routinely references
    ``{runtime.run_id}``. These fields are derived, so the template they were
    authored with never reached a digest and rendering cannot move one.

    Nothing about the invocation is written *into* the configuration here. The
    active command reaches templates as ``{:command}`` instead: a configuration is
    not per-command, and recording one in it would fork the configuration index
    between two commands reading the same authored file.
    """
    warnings: list[str] = []
    root_type = type(config)

    for path in marked_field_paths(root_type, TemplatedMarker):
        if WILDCARD in path:
            continue  # a templated field inside a container is the project's to fill
        dotted = ".".join(path)
        value = _get(config, dotted)
        if not isinstance(value, str) or "{" not in value:
            continue
        rendered = render_template(
            value,
            config.model_dump(mode="json"),
            context=context,
            registry=registry,
        )
        warnings.extend(rendered.warnings)
        _set(config, dotted, rendered.text)
    return tuple(warnings)


def _phase6_target(
    config: BaseModel,
    command: CommandSpec,
    composed: Mapping[str, Any],
    *,
    context: TemplateContext,
    registry: TemplateRegistry,
    unassigned: tuple[str, ...],
    replay: bool,
    cwd: Path,
) -> tuple[Path | None, tuple[str, ...]]:
    """Render the active command's target directory. Only the active one."""
    if command.target is None:
        return None, ()

    spec = command.target
    literal = _get(config, spec.resolved)
    template = _get(config, spec.template)

    if (
        not replay
        and _authored(composed, spec.resolved)
        and _authored(composed, spec.template)
    ):
        raise RefusalError(
            f"{spec.resolved} and {spec.template} are both authored; supply one or "
            "the other. (A replayed resolved configuration legitimately carries "
            "both, and is exempt.)"
        )

    root = _get(config, spec.root)
    if root is None:
        raise RefusalError(f"{spec.root} is required to place output")
    root = Path(root)

    warnings: list[str] = []
    if literal is not None:
        # Read the authored text where available: pathlib has already discarded a
        # leading "./" from the validated value.
        authored = _authored_value(composed, spec.resolved)
        text = str(authored) if authored is not None else str(literal)
    elif template is not None:
        # Before rendering, against the template actually in force. The registry's
        # import-time check only sees the committed schema default, so a composed
        # configuration that overrides the template escapes it entirely — and an
        # earlier version of this check ran *after* rendering and compared dotted
        # paths against the rendered text, which can never match.
        referenced = template_value_paths(template)
        for dotted in unassigned:
            if dotted in referenced:
                raise RefusalError(
                    f"{command.invocation} target template names {dotted!r}, which is "
                    "fed by a hash deferred to work. The output directory must be "
                    "knowable before work begins."
                )
        rendered = render_template(
            template,
            config.model_dump(mode="json"),
            context=context,
            registry=registry,
            path_fields=frozenset(
                ".".join(p) for p in marked_field_paths(type(config), PathMarker)
            ),
        )
        warnings.extend(rendered.warnings)
        text = rendered.text
    else:
        raise RefusalError(f"one of {spec.resolved} or {spec.template} must be set")

    rendered_value = (cwd / text).resolve() if is_cwd_relative(text) else Path(text)

    _set(config, spec.resolved, rendered_value)
    absolute = anchor(rendered_value, base=root)

    refusal = path_length_refusal(absolute)
    if refusal is not None:
        raise RefusalError(refusal)
    return absolute, tuple(warnings)


def _phase7_leaves(
    config: BaseModel,
    command: CommandSpec,
    *,
    context: TemplateContext,
    registry: TemplateRegistry,
    replay: bool,
) -> None:
    """Resolve the target block's leaves. Each stays relative to its block."""
    if command.target is None:
        return
    block_path = command.target.resolved.rsplit(".", 1)[0]
    block = _get(config, block_path)
    if not isinstance(block, OutputBlock):
        return

    for name, leaf in _iter_leaves(block):
        if not replay and leaf.dir is not None and leaf.dir_template is not None:
            raise RefusalError(
                f"{block_path}.{name} sets both dir and dir_template; author one"
            )
        if leaf.dir is None and leaf.dir_template is not None:
            rendered = render_template(
                leaf.dir_template,
                config.model_dump(mode="json"),
                context=context,
                registry=registry,
            )
            leaf.dir = Path(rendered.text)
        if leaf.enabled and leaf.dir is None:
            raise RefusalError(
                f"{block_path}.{name} is enabled but has no directory; give it a "
                "dir or a dir_template, or disable it"
            )


def _absolute_view(portable: BaseModel, command: CommandSpec, *, root: Path) -> BaseModel:
    """The same configuration with output paths joined.

    A deep copy plus a rewrite of the paths the walkers already know about — fifteen
    lines, no new types, and no interaction with validation. A path type carrying
    its own anchor was considered and rejected: the anchor is context rather than
    data, so it does not survive ``model_validate`` or ``model_copy`` and vanishes
    silently.
    """
    absolute = portable.model_copy(deep=True)
    if command.target is None:
        return absolute

    spec = command.target
    stored = _get(absolute, spec.resolved)
    if stored is None:
        return absolute
    target = anchor(Path(stored), base=root)
    _set(absolute, spec.resolved, target)

    block = _get(absolute, spec.resolved.rsplit(".", 1)[0])
    if isinstance(block, OutputBlock):
        for _, leaf in _iter_leaves(block):
            if leaf.dir is not None:
                leaf.dir = anchor(leaf.dir, base=target)
    return absolute


# --------------------------------------------------------------------------- #
# the resolver
# --------------------------------------------------------------------------- #


def resolve(
    composed: Mapping[str, Any],
    *,
    root_type: type[BaseModel],
    command: CommandSpec,
    hashes: HashRegistry,
    context: TemplateContext,
    templates: TemplateRegistry | None = None,
    cwd: Path | None = None,
    existing_target: Path | None = None,
    replay: bool = False,
) -> Resolution:
    """Walk the eight phases and return a pure :class:`Resolution`.

    ``existing_target`` is how an augmenting command inherits its run directory
    rather than re-rendering the template — which is also what lets configuration
    indices deduplicate, since a re-rendered ``{:timestamp}`` would make every
    invocation's resolved configuration unique.
    """
    registry = templates or TemplateRegistry()
    if "command" not in context.extras:
        context = replace(context, extras={**context.extras, "command": command.slug})
    base = (cwd or Path.cwd()).resolve()
    warnings: list[str] = []

    # 1 — validate
    config = root_type.model_validate(dict(composed))

    # 2 — input paths. Output blocks are skipped; 6 and 7 own those.
    # Recognition goes through the shared walker rather than a local isinstance test:
    # a narrower test misses `Block | None` and `Annotated[Block, ...]`, and the
    # consequence is silent — phase 2 absolutizes the block's directory against the
    # working directory, phase 6 then reads it as a pinned literal, and the run lands
    # next to the shell instead of under the output root.
    _phase2_input_paths(config, cwd=base, skip=frozenset(output_block_fields(root_type)))

    # 3 — config-only hashes for this command
    result = hashes.compute(
        {"config": config, "command": command.slug},
        only=tuple(command.hashes) or None,
        input_reading=False,
    )

    # 4 — identities into their declared fields
    unassigned = _phase4_identities(config, result.entries, result.deferred)

    # 5 — template-valued fields, so the target may reference them
    warnings.extend(
        _phase5_templated(config, context=context, registry=registry)
    )

    # 6 — the target
    if command.mode == "augment":
        if existing_target is None:
            raise RefusalError(
                f"{command.invocation} augments an existing run; its directory must "
                "be supplied rather than rendered"
            )
        target: Path | None = existing_target
        if command.target is not None:
            root_value = Path(_get(config, command.target.root))
            try:
                stored = existing_target.relative_to(root_value.resolve())
            except ValueError:
                stored = existing_target
            _set(config, command.target.resolved, stored)
            # The template is left exactly as authored. Nulling it to signal "not
            # rendered" would both discard the authored record and fork the
            # configuration index: an augmenting command over an unchanged
            # configuration must land on the same index as the run that produced it.
    else:
        target, target_warnings = _phase6_target(
            config,
            command,
            composed,
            context=context,
            registry=registry,
            unassigned=unassigned,
            replay=replay,
            cwd=base,
        )
        warnings.extend(target_warnings)

    # 7 — leaves under the target
    _phase7_leaves(config, command, context=context, registry=registry, replay=replay)

    # 8 — revalidate. Project validators run a second time here, against a tree
    # whose identities are set and whose input paths are absolute.
    portable = root_type.model_validate(config.model_dump(mode="python"))

    root_dir = (
        Path(_get(portable, command.target.root)).resolve()
        if command.target is not None
        else base
    )
    absolute = _absolute_view(portable, command, root=root_dir)

    return Resolution(
        config=absolute,
        portable=portable,
        command=command,
        context=context,
        hashes=result.entries,
        deferred=result.deferred,
        target=target,
        warnings=tuple(warnings),
    )
