"""Hash declarations, computed entries, and the per-hash artifact.

A project declares its hashes; Honbu computes, persists, and verifies them
identically in every case. Four declaration forms, because field selection alone
turns out to cover the configuration identity and almost nothing else — every
domain hash in the sibling projects synthesizes a value, pins a versioned
constant, or digests something outside the configuration:

* :class:`FieldHash` — selected configuration fields. Declarative data, so the
  drift-prone case is reviewable at a glance and validated at import.
* :class:`SourceHash` — a project callable returning the source. Carries its own
  recipe version *inside* the source, so the version and the hashed bytes cannot
  disagree.
* :class:`ByteHash` — a digest over file bytes. No source object exists, so it is
  unverifiable once the file is gone, and that is recorded rather than faked.
* :class:`FannedHash` — one entry per key, for a per-head identity whose key set
  comes from the configuration and so cannot be written out by hand.

What a declaration does *not* carry is which operations it gates. That is policy,
and freezing policy into a historical artifact would mean rewriting old runs to
change your mind; the requesting operation names its gates instead.

Absent inputs give a **deferred** hash, not an error — which is what lets a
byte-reading dataset identity be optional and lets read-only inspection resolve
everything else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.errors import DeclarationError, RefusalError
from honbu.core.canonical import (
    CANONICAL_ENCODER_VERSION,
    file_digest,
    hash_source,
)
from honbu.core.io import atomic_write_json, read_json
from honbu.core.markers import (
    check_field_path,
    excluded_source_paths,
    identity_bindings,
    is_resolved_only,
    parse_dotted,
    prune_path,
)
from honbu.core.model import StrictModel
from honbu.core.paths import sanitize_path_component

HASHES_DIRNAME = "hashes"


class HashEntry(StrictModel):
    """One computed hash, as persisted to ``config/hashes/<name>.json``.

    ``source is None`` *means* the digest was taken over bytes — no separate kind
    flag is needed, because a structural hash always has a source and a byte
    digest never does. ``provenance`` is a free-form project label recording how
    strongly the identity was established (``uri_size``, ``uri_etag``,
    ``manifest_content``, …); Honbu records it and never interprets it.
    """

    name: str
    value: str
    #: Set when the declaration fanned out — the mapping key or sequence index this
    #: entry covers. Recorded in the artifact rather than left to be parsed out of
    #: the filename, so a reader never has to guess where the name ends.
    key: str | None = None
    version: str | None = None
    provenance: str | None = None
    encoder_version: str = CANONICAL_ENCODER_VERSION
    source: Any | None = None

    @property
    def entry_id(self) -> str:
        """How this entry is addressed: ``head_hash`` or ``head_hash.dorsal``."""
        return self.name if self.key is None else f"{self.name}.{self.key}"


@dataclass(frozen=True)
class FieldHash:
    """A hash keyed to selected configuration fields.

    ``include`` is dotted paths — whole blocks or single fields. A mapping keys it
    per command, which is how the configuration identity narrows to the blocks a
    command actually reads. ``exclude`` prunes subfields from within the included
    set.

    Marker-driven exclusions are applied *after* this, always, and are never
    declared here: that is what stops a newly marked field from being re-admitted
    to a digest by an include set nobody revisited.
    """

    name: str
    include: Sequence[str] | Mapping[str, Sequence[str]]
    version: str | None = None
    exclude: Sequence[str] = ()
    provenance: str | None = None


@dataclass(frozen=True)
class SourceHash:
    """A hash whose source the project builds: computed, constant, or data-derived.

    The source MUST carry its own ``version`` key — Honbu copies it into the entry
    rather than inventing one, so a bumped declaration can never claim a version
    the bytes do not match.

    ``reads_inputs`` declares that computing this touches the filesystem or the
    network. Such a hash is **always** deferred past resolution, whether or not its
    inputs happen to be present. Two reasons: a minutes-long manifest scan must not
    run before the collision check, or you wait two minutes to be told the output
    directory is occupied; and the deferred set stays derivable from declarations
    alone, so ``inspect`` reports the same thing on every machine.
    """

    name: str
    build: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
    provenance: str | None = None
    reads_inputs: bool = False


@dataclass(frozen=True)
class ByteHash:
    """A hash over file bytes. Reads inputs by definition, so always deferred."""

    name: str
    locate: Callable[[Mapping[str, Any]], Path | None]
    provenance: str | None = None
    reads_inputs: bool = True


@dataclass(frozen=True)
class FannedHash:
    """One declaration, one entry per key — the per-head identity case.

    ``build`` returns a mapping of key to source, or a sequence when the collection
    has no natural key, in which case entries are keyed by index. Each source
    carries its own ``version``, exactly as :class:`SourceHash` requires.

    The alternative was a per-head declaration written out by hand, which cannot
    work: the head names come from the configuration, so the set is not known until
    a configuration exists.
    """

    name: str
    build: Callable[
        [Mapping[str, Any]],
        Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
    ]
    provenance: str | None = None
    reads_inputs: bool = False

    def __post_init__(self) -> None:
        if "." in self.name:
            # Otherwise `head_hash.dorsal` would be ambiguous between a fanned
            # entry and a base name that happens to contain a dot.
            raise DeclarationError(
                f"a fanned hash name may not contain a dot: {self.name!r}"
            )


HashDeclaration = FieldHash | SourceHash | ByteHash | FannedHash


@dataclass(frozen=True)
class HashResult:
    entries: dict[str, HashEntry] = field(default_factory=dict)
    deferred: tuple[str, ...] = ()


def declared_path_exists(model_cls: type[BaseModel], dotted: str) -> bool:
    """Whether ``dotted`` names a real field, traversing direct submodels only.

    The boolean face of :func:`check_field_path` with container traversal off —
    one traversal, not two. A hash's include set and a command's write scope name
    whole containers rather than reaching inside them; a sweep axis is the caller
    that legitimately does the opposite.
    """
    return check_field_path(model_cls, dotted) is None


def select_paths(dump: Mapping[str, Any], includes: Sequence[str]) -> dict[str, Any]:
    """Copy the included dotted paths out of a dumped configuration tree.

    A path absent from the dump is skipped rather than failing: ``exclude_none``
    legitimately drops null fields. A path absent from the *schema* is a different
    problem, caught when the registry is constructed.
    """
    from copy import deepcopy

    out: dict[str, Any] = {}
    for dotted in includes:
        segments = dotted.split(".")
        source: Any = dump
        target = out
        for segment in segments[:-1]:
            if not isinstance(source, Mapping) or segment not in source:
                source = None
                break
            source = source[segment]
            target = target.setdefault(segment, {})
        leaf = segments[-1]
        if isinstance(source, Mapping) and leaf in source:
            target[leaf] = deepcopy(source[leaf])
    return out


def project_fields(declaration: FieldHash, context: Mapping[str, Any]) -> dict[str, Any]:
    """Build a :class:`FieldHash` source from the resolved configuration."""
    config = context["config"]
    include = declaration.include
    if isinstance(include, Mapping):
        command = context.get("command")
        if command is None:
            raise DeclarationError(f"{declaration.name} is command-scoped; no command given")
        if command not in include:
            raise DeclarationError(
                f"{declaration.name} declares no include set for command {command!r}"
            )
        include = include[command]

    source = select_paths(config.model_dump(mode="json", exclude_none=True), include)
    for dotted in declaration.exclude:
        prune_path(source, parse_dotted(dotted))
    for path in excluded_source_paths(type(config)):
        prune_path(source, path)
    if declaration.version is not None:
        source["version"] = declaration.version
    return source


class HashRegistry:
    """A project's declared hashes.

    Passing ``root_type`` validates every declared field path against the schema at
    construction — i.e. at import — because a typo like ``include=["trainng"]``
    would otherwise silently drop a whole block from identity and produce a
    perfectly stable, perfectly wrong digest.
    """

    def __init__(
        self,
        declarations: Sequence[HashDeclaration],
        *,
        root_type: type[BaseModel] | None = None,
    ) -> None:
        names = [declaration.name for declaration in declarations]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise DeclarationError(f"duplicate hash declarations: {sorted(duplicates)}")
        self._declarations = tuple(declarations)
        if root_type is not None:
            self._validate_paths(root_type)

    def _validate_paths(self, root_type: type[BaseModel]) -> None:
        for declaration in self._declarations:
            if not isinstance(declaration, FieldHash):
                continue
            include = declaration.include
            groups = include.values() if isinstance(include, Mapping) else [include]
            for group in groups:
                for dotted in (*group, *declaration.exclude):
                    if not declared_path_exists(root_type, dotted):
                        raise DeclarationError(
                            f"{declaration.name} declares path {dotted!r}, which is not "
                            f"a field of {root_type.__name__}"
                        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(declaration.name for declaration in self._declarations)

    @property
    def fanned_names(self) -> tuple[str, ...]:
        """Names that yield many entries, so no single field can receive one."""
        return tuple(
            declaration.name
            for declaration in self._declarations
            if isinstance(declaration, FannedHash)
        )

    @property
    def deferred_names(self) -> tuple[str, ...]:
        """Names deferred past resolution, known from the declarations alone."""
        return tuple(
            declaration.name
            for declaration in self._declarations
            if getattr(declaration, "reads_inputs", False)
        )

    def declaration(self, name: str) -> HashDeclaration:
        for declaration in self._declarations:
            if declaration.name == name:
                return declaration
        raise DeclarationError(f"no hash declared as {name!r}")

    def compute(
        self,
        context: Mapping[str, Any],
        *,
        only: Sequence[str] | None = None,
        input_reading: bool = False,
    ) -> HashResult:
        """Compute declared hashes.

        ``only`` narrows to the active command's declared set, so ``inspect
        config`` does not pay for a domain hash it will not report.
        ``input_reading`` is False during resolution: those are reported deferred
        and completed later, from the run.
        """
        entries: dict[str, HashEntry] = {}
        deferred: list[str] = []
        for declaration in self._declarations:
            if only is not None and declaration.name not in only:
                continue
            if getattr(declaration, "reads_inputs", False) and not input_reading:
                deferred.append(declaration.name)
                continue
            match declaration:
                case ByteHash(name=name, locate=locate, provenance=provenance):
                    path = locate(context)
                    if path is None:
                        deferred.append(name)
                        continue
                    entries[name] = HashEntry(
                        name=name,
                        value=file_digest(path),
                        provenance=provenance,
                        source=None,
                    )
                case SourceHash(name=name, build=build, provenance=provenance):
                    source = build(context)
                    if source is None:
                        deferred.append(name)
                        continue
                    if "version" not in source:
                        raise DeclarationError(f"{name}: source must declare a version")
                    entries[name] = HashEntry(
                        name=name,
                        value=hash_source(source),
                        version=str(source["version"]),
                        provenance=provenance,
                        source=source,
                    )
                case FannedHash(name=name, build=build, provenance=provenance):
                    fanned = build(context)
                    if fanned is None:
                        deferred.append(name)
                        continue
                    for key, source in _fanned_items(name, fanned):
                        if "version" not in source:
                            raise DeclarationError(
                                f"{name}.{key}: source must declare a version"
                            )
                        entry = HashEntry(
                            name=name,
                            key=key,
                            value=hash_source(source),
                            version=str(source["version"]),
                            provenance=provenance,
                            source=source,
                        )
                        entries[entry.entry_id] = entry
                case FieldHash() as field_hash:
                    source = project_fields(field_hash, context)
                    entries[field_hash.name] = HashEntry(
                        name=field_hash.name,
                        value=hash_source(source),
                        version=field_hash.version,
                        provenance=field_hash.provenance,
                        source=source,
                    )
        return HashResult(entries=entries, deferred=tuple(deferred))


def validate_identity_fields(
    root_type: type[BaseModel], registry: HashRegistry
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Cross-check every ``FromHash`` field against the registry.

    Two failures, both at construction. A field naming a hash nobody declares would
    silently stay null and render as ``None`` inside a directory name. A field that
    is not resolved-only would be an *authored* value assigned after its digest was
    computed, so the recorded digest would describe a configuration that never ran.
    """
    bindings = identity_bindings(root_type)
    known = set(registry.names)
    for path, hash_name in bindings:
        dotted_path = ".".join(path)
        if hash_name not in known:
            raise DeclarationError(
                f"{root_type.__name__}.{dotted_path} declares FromHash({hash_name!r}), "
                f"which no hash declares; declared: "
                f"{', '.join(sorted(known)) or '(none)'}"
            )
        if hash_name in registry.fanned_names:
            raise DeclarationError(
                f"{root_type.__name__}.{dotted_path} receives {hash_name!r}, which "
                "fans out to one entry per key; a single field cannot hold many "
                "digests"
            )
        if not is_resolved_only(root_type, path):
            raise DeclarationError(
                f"{root_type.__name__}.{dotted_path} receives a hash but is not "
                "resolved-only; assigning an authored field after its digest was "
                "computed would make the digest describe a configuration that never ran"
            )
    return bindings


def _fanned_items(
    name: str,
    fanned: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> list[tuple[str, Mapping[str, Any]]]:
    """Key/source pairs from a fanned build result.

    A mapping supplies its own keys; a sequence has none, so entries are keyed by
    index. Indices are not zero-padded: entries are addressed by name, not read off
    a directory listing.
    """
    if isinstance(fanned, Mapping):
        return [(str(key), value) for key, value in sorted(fanned.items())]
    if isinstance(fanned, (list, tuple)):
        return [(str(index), value) for index, value in enumerate(fanned)]
    raise DeclarationError(
        f"{name}: a fanned build must return a mapping or a sequence, not "
        f"{type(fanned).__name__}"
    )


def artifact_filename(entry: HashEntry) -> str:
    """The artifact filename for an entry, with the key sanitized as a component."""
    if entry.key is None:
        return f"{entry.name}.json"
    return f"{entry.name}.{sanitize_path_component(entry.key)}.json"


def write_hash_entries(config_dir: Path, entries: Mapping[str, HashEntry]) -> Path:
    """Persist one artifact per hash under ``<config_dir>/hashes/``.

    Stable filenames, value inside: comparing two runs by hand is what you do when
    a digest surprises you, and identical paths make that a plain ``diff``.

    A fanned key becomes part of a filename, so it is sanitized — and two keys that
    sanitize to the same name are refused rather than silently overwriting one
    another, which would leave a plausible-looking artifact for a head that never
    got one.
    """
    target = config_dir / HASHES_DIRNAME
    target.mkdir(parents=True, exist_ok=True)

    planned: dict[str, HashEntry] = {}
    for entry in entries.values():
        filename = artifact_filename(entry)
        clash = planned.get(filename)
        if clash is not None and clash.key != entry.key:
            raise RefusalError(
                f"{entry.name}: keys {clash.key!r} and {entry.key!r} both sanitize to "
                f"{filename!r}; rename one so each entry gets its own artifact"
            )
        planned[filename] = entry

    for filename, entry in planned.items():
        atomic_write_json(target / filename, entry.model_dump(mode="json"))
    return target


def read_hash_entries(config_dir: Path) -> dict[str, HashEntry]:
    """Read every persisted hash artifact. Unknown names are the caller's to ignore."""
    target = config_dir / HASHES_DIRNAME
    if not target.is_dir():
        return {}
    entries: dict[str, HashEntry] = {}
    for path in sorted(target.glob("*.json")):
        entry = HashEntry.model_validate(read_json(path))
        entries[entry.entry_id] = entry
    return entries


def verify_hash_entries(entries: Mapping[str, HashEntry]) -> dict[str, bool | None]:
    """Re-hash each stored source. None where the entry cannot be verified at all.

    The tri-state is the point: a byte digest with its file gone is neither correct
    nor incorrect, and collapsing that into a bool is what forces a caller to guess.
    """
    return {
        name: None if entry.source is None else hash_source(entry.source) == entry.value
        for name, entry in entries.items()
    }
