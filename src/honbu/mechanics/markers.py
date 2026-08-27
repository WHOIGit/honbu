"""Field markers and the walkers that act on them.

A marker is a type-level declaration. Marking a field is the *only* thing a
project does to keep it out of behavioral sources or to have it absolutized — no
hashing module is edited, no list is maintained, so a marked field added next year
is excluded by construction.

The walker's one subtlety is container traversal. A marked field inside
``dict[str, Block]`` or ``list[Block]`` sits one container step below its field,
and a walker that forgets that step emits a path which silently matches nothing
during pruning — the field stays in the digest and nothing complains. Container
steps are therefore represented explicitly as :data:`WILDCARD`, and the pruner
expands them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel


class PathMarker:
    """A portable filesystem path.

    Excluded from every behavioral source and absolutized by the resolver: where
    a run happens to live is not part of what it computes.
    """


class DerivedMarker:
    """A value Honbu generates — an identity, an ID, a digest.

    Excluded from behavioral sources, because including it would hash its own
    inputs a second time and make the identity self-referential.
    """


class ResolvedOnlyMarker:
    """May be absent from authored configuration; the resolver populates it."""


class TemplatedMarker:
    """This field's own value is a template, rendered in place by the resolver.

    Distinct from an output block's ``dir_template``/``dir`` pair, which is two
    fields because authoring a literal directory is a real alternative. Here there
    is one field and the template *is* the authored form — ``run_id`` is
    ``"{:coolname}"`` in the file and ``"eager-goldfish"`` after resolution.

    Rendering happens before any output path renders, so a run directory may
    reference the value. Such a field is excluded from digests: the authored
    template and the rendered value would otherwise disagree about what the run
    was, and which one a digest saw would depend on phase ordering.
    """


class IdentityMarker:
    """Record this field's value in the run manifest's identities.

    Purely a recording declaration, orthogonal to where the value came from. Not
    every hash warrants an identity, and not every identity derives from a hash —
    a fresh per-invocation ``run_id`` derives from nothing — so the framework
    cannot infer the set and does not try.
    """


class FromHash:
    """This field receives a named hash's digest during resolution.

    Parameterized, unlike the other markers, so one instance exists per use. It
    lives on the field rather than in a side table of dotted paths for three
    reasons: there is no path to mistype, the walkers below already find marked
    fields through containers, and a project subclass that adds one is picked up
    without a further declaration.

    The digest is what lands here. A human-readable form is a template concern —
    ``{runtime.config_hash:coolname}`` — so a separate id field is usually
    redundant and earns its place only when the id is a domain value used outside
    templates.
    """

    __slots__ = ("hash_name",)

    def __init__(self, hash_name: str) -> None:
        self.hash_name = hash_name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"FromHash({self.hash_name!r})"


PORTABLE_PATH = PathMarker()
DERIVED = DerivedMarker()
RESOLVED_ONLY = ResolvedOnlyMarker()
TEMPLATED = TemplatedMarker()
IDENTITY = IdentityMarker()

PortablePath = Annotated[Path, PORTABLE_PATH]
ResolvedString = Annotated[str | None, DERIVED, RESOLVED_ONLY]
ResolvedPath = Annotated[Path | None, PORTABLE_PATH, DERIVED, RESOLVED_ONLY]
#: A template rendered in place. Derived, so the template never reaches a digest.
TemplatedString = Annotated[str, DERIVED, TEMPLATED]
#: A template rendered in place and recorded in the manifest as a run identity.
TemplatedIdentity = Annotated[str, DERIVED, TEMPLATED, IDENTITY]

#: One container step — a mapping value or a sequence element.
WILDCARD = "*"

FieldPath = tuple[str, ...]

_EXCLUDED_FROM_SOURCES = (PathMarker, DerivedMarker, TemplatedMarker)


def _unwrap(annotation: Any) -> Any:
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _has_marker(annotation: Any, marker_type: type) -> bool:
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if any(isinstance(marker, marker_type) for marker in args[1:]):
            return True
        return _has_marker(args[0], marker_type)
    return any(_has_marker(arg, marker_type) for arg in get_args(annotation))


def _contains_type(annotation: Any, target: type) -> bool:
    annotation = _unwrap(annotation)
    if annotation is target:
        return True
    return any(_contains_type(arg, target) for arg in get_args(annotation))


def _descend(annotation: Any) -> list[tuple[FieldPath, type[BaseModel]]]:
    """Models reachable from ``annotation``, with the path steps to reach them.

    A mapping value or sequence element sits one container step below the field,
    so it contributes a WILDCARD. A union contributes no step.
    """
    annotation = _unwrap(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [((), annotation)]
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (dict, Mapping) and len(args) == 2:
        return [((WILDCARD, *steps), model) for steps, model in _descend(args[1])]
    if origin in (list, tuple, set, frozenset, Sequence):
        return [
            ((WILDCARD, *steps), model)
            for arg in args
            if arg is not Ellipsis
            for steps, model in _descend(arg)
        ]
    if origin in (Union, UnionType):
        return [
            pair
            for arg in args
            if arg is not type(None)
            for pair in _descend(arg)
        ]
    return []


def traversable_submodel(annotation: Any) -> type[BaseModel] | None:
    """The one submodel reachable from ``annotation`` without a container step.

    Used to validate declared dotted paths. Returns None for a container or an
    ambiguous union, so a declaration must name the container field itself rather
    than reaching inside it — anything finer belongs in a project-built source.
    """
    direct = [model for steps, model in _descend(annotation) if not steps]
    return direct[0] if len(direct) == 1 else None


def _walk(
    model_cls: type[BaseModel],
    predicate: Callable[[Any, Any], bool],
    prefix: FieldPath,
    chain: frozenset[type],
) -> Iterator[FieldPath]:
    if model_cls in chain:  # a recursive schema; stop rather than hang
        return
    deeper = chain | {model_cls}
    for name, field in model_cls.model_fields.items():
        here = (*prefix, name)
        if predicate(field, field.annotation):
            yield here  # the whole subtree is marked; do not descend into it
            continue
        for steps, submodel in _descend(field.annotation):
            yield from _walk(submodel, predicate, (*here, *steps), deeper)


@lru_cache(maxsize=None)
def marked_field_paths(model_cls: type[BaseModel], marker_type: type) -> tuple[FieldPath, ...]:
    """Every field carrying ``marker_type``, by dotted path with container steps.

    Memoized: schemas are static after import, and sweeps re-resolve per child.
    """

    def carries(field: Any, annotation: Any) -> bool:
        return any(isinstance(m, marker_type) for m in field.metadata) or _has_marker(
            annotation, marker_type
        )

    return tuple(_walk(model_cls, carries, (), frozenset()))


def _marker_of(field: Any, annotation: Any, marker_type: type) -> Any | None:
    """The marker instance on a field, wherever it was attached."""
    for marker in field.metadata:
        if isinstance(marker, marker_type):
            return marker
    stack = [annotation]
    while stack:
        current = stack.pop()
        if get_origin(current) is Annotated:
            args = get_args(current)
            for marker in args[1:]:
                if isinstance(marker, marker_type):
                    return marker
            stack.append(args[0])
        else:
            stack.extend(get_args(current))
    return None


def _walk_marked(
    model_cls: type[BaseModel],
    marker_type: type,
    prefix: FieldPath,
    chain: frozenset[type],
) -> Iterator[tuple[FieldPath, Any]]:
    if model_cls in chain:
        return
    deeper = chain | {model_cls}
    for name, field in model_cls.model_fields.items():
        here = (*prefix, name)
        marker = _marker_of(field, field.annotation, marker_type)
        if marker is not None:
            yield here, marker
            continue
        for steps, submodel in _descend(field.annotation):
            yield from _walk_marked(submodel, marker_type, (*here, *steps), deeper)


@lru_cache(maxsize=None)
def identity_bindings(model_cls: type[BaseModel]) -> tuple[tuple[FieldPath, str], ...]:
    """Every field declaring a hash it receives, as (path, hash name).

    The resolver's phase 4 is a loop over this. Memoized like the other walks,
    since schemas are static after import and sweeps re-resolve per child.
    """
    return tuple(
        (path, marker.hash_name)
        for path, marker in _walk_marked(model_cls, FromHash, (), frozenset())
    )


@lru_cache(maxsize=None)
def identity_field_names(model_cls: type[BaseModel]) -> tuple[tuple[str, FieldPath], ...]:
    """Identity fields as (recorded name, path), refusing duplicate leaf names.

    Recorded under the leaf name rather than the dotted path, because the manifest
    is the file people grep. Two identity fields sharing a leaf are refused so the
    keys stay unambiguous — two fields both called ``run_id`` in different blocks
    is confusing on its own terms.
    """
    found: list[tuple[str, FieldPath]] = []
    seen: dict[str, FieldPath] = {}
    for path, _ in _walk_marked(model_cls, IdentityMarker, (), frozenset()):
        leaf = path[-1]
        if leaf in seen:
            raise ValueError(
                f"{model_cls.__name__}: identity fields {dotted(seen[leaf])!r} and "
                f"{dotted(path)!r} share the name {leaf!r}; rename one"
            )
        seen[leaf] = path
        found.append((leaf, path))
    return tuple(found)


def is_resolved_only(model_cls: type[BaseModel], path: FieldPath) -> bool:
    """Whether ``path`` carries the resolved-only marker.

    Assigning an *authored* field during resolution would let the recorded digest
    describe a configuration that never ran, so identity fields are checked against
    this at construction rather than trusted.
    """
    current: Any = model_cls
    for index, segment in enumerate(path):
        if segment == WILDCARD:
            return False
        fields = getattr(current, "model_fields", None)
        if not fields or segment not in fields:
            return False
        field = fields[segment]
        if index == len(path) - 1:
            return _marker_of(field, field.annotation, ResolvedOnlyMarker) is not None
        current = traversable_submodel(field.annotation)
        if current is None:
            return False
    return False


@lru_cache(maxsize=None)
def excluded_source_paths(model_cls: type[BaseModel]) -> tuple[FieldPath, ...]:
    """Paths never permitted in a behavioral source: portable paths and derived ids."""
    return tuple(
        path for marker in _EXCLUDED_FROM_SOURCES for path in marked_field_paths(model_cls, marker)
    )


@lru_cache(maxsize=None)
def unmarked_path_fields(model_cls: type[BaseModel]) -> tuple[FieldPath, ...]:
    """Path-typed fields missing the portable-path marker.

    A guard, meant to be asserted empty in a project's test suite: without it a
    newly added path field enters behavioral sources silently. Note it only has
    teeth once paths are typed as ``Path``; a path held in a bare ``str`` is
    invisible here.
    """

    def offending(field: Any, annotation: Any) -> bool:
        return _contains_type(annotation, Path) and not (
            any(isinstance(m, PathMarker) for m in field.metadata)
            or _has_marker(annotation, PathMarker)
        )

    return tuple(_walk(model_cls, offending, (), frozenset()))


def prune_path(node: Any, path: FieldPath) -> None:
    """Remove ``path`` from a dumped tree in place, expanding WILDCARD steps."""
    if not path:
        return
    head, rest = path[0], path[1:]
    if head == WILDCARD:
        if isinstance(node, Mapping):
            children: Any = node.values()
        elif isinstance(node, list):
            children = node
        else:
            return
        for child in children:
            prune_path(child, rest)
        return
    if not isinstance(node, dict) or head not in node:
        return
    if rest:
        prune_path(node[head], rest)
    else:
        node.pop(head)


def dotted(path: FieldPath) -> str:
    return ".".join(path)


_IDENTIFIER_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def parse_dotted(value: str) -> FieldPath:
    """Parse a declared dotted path, rejecting anything that is not field names."""
    segments = tuple(value.split("."))
    for segment in segments:
        if not _IDENTIFIER_SEGMENT.match(segment):
            raise ValueError(f"{value!r} is not a dotted field path")
    return segments
