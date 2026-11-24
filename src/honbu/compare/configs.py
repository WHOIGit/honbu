"""Schema-aware, N-way configuration comparison (D26).

The comparison engine works on mappings so callers decide how operands are
obtained (composition, a saved run, or a file). A root schema unlocks default
normalization and marker-defined projections; a hash registry evaluates every
pure (``reads_inputs=False``) declaration at ``by_hash`` while reporting
input-reading declarations honestly as opaque.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from honbu.core.hashes import FieldHash, HashRegistry
from honbu.core.markers import (
    WILDCARD,
    ResolvedOnlyMarker,
    excluded_source_paths,
    marked_field_paths,
    prune_path,
)
from honbu.errors import DeclarationError, RefusalError
from honbu.flow.compose import (
    ComposeSpec,
    compose_config,
    compose_file,
    composed_config_path,
    resolved_config_path,
)
from honbu.flow.project import ProjectSpec

ComparisonLevel = Literal["literal", "authored", "behavioral", "by_hash"]
OperandSource = Literal["default", "file", "composed", "resolved"]


class _Absent:
    def __repr__(self) -> str:
        return "<absent>"


#: A value that cannot be confused with an authored string such as ``"<absent>"``.
ABSENT = _Absent()


@dataclass(frozen=True)
class ConfigOperand:
    """One configuration source plus overrides specific to that source.

    ``fragment`` identifies a configuration-group file rather than a complete
    root. Such an operand deliberately supports literal comparison only: there
    is no root to validate or project through the marker schema.
    """

    source: OperandSource = "default"
    reference: Path | str | None = None
    overrides: tuple[str, ...] = ()
    extra_dirs: tuple[Path | str, ...] = ()
    config_index: int = 1
    label: str | None = None
    fragment: bool = False


@dataclass(frozen=True)
class Difference:
    """One path on which at least two operands disagree."""

    path: str
    values: tuple[Any, ...]
    moved_hashes: tuple[str, ...] = ()
    excluded: bool = False


@dataclass(frozen=True)
class HashComparison:
    """Per-hash evaluation across compared operands at ``by_hash``."""

    name: str
    equal: bool
    values: tuple[str | None, ...]
    source_differences: tuple[Difference, ...] = ()


@dataclass(frozen=True)
class Comparison:
    """A structured comparison suitable for project-specific rendering."""

    level: ComparisonLevel
    labels: tuple[str, ...]
    differences: tuple[Difference, ...]
    residual_paths: tuple[str, ...] = ()
    opaque_hashes: tuple[str, ...] = ()
    hash_comparisons: tuple[HashComparison, ...] = ()


def normalize_operand(
    operand: ConfigOperand,
    *,
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Compose one declared operand to the mapping comparison operates on.

    The source names cover the four configuration views: ``default``, ``file``,
    ``composed`` (a prior run's ``composed.yaml``), and ``resolved`` (a prior
    run's ``resolved.yaml``). Both ``composed`` and ``resolved`` accept a run
    directory directly and honor ``operand.config_index``.
    """
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    if operand.source == "default":
        if operand.reference is not None:
            raise RefusalError("a default comparison operand takes no reference")
        if spec is None:
            raise DeclarationError(
                "normalizing a 'default' operand requires compose_spec or project"
            )
        return compose_config(
            spec,
            overrides=operand.overrides,
            extra_dirs=operand.extra_dirs,
            cwd=cwd,
        )
    if operand.reference is None:
        raise RefusalError(
            f"comparison operand source {operand.source!r} needs a reference"
        )

    reference = Path(operand.reference)
    if operand.source == "file":
        return compose_file(
            reference, overrides=operand.overrides, extra_dirs=operand.extra_dirs
        )
    if operand.source == "resolved":
        path = (
            resolved_config_path(reference, index=operand.config_index)
            if reference.is_dir()
            else reference
        )
        return compose_file(
            path, overrides=operand.overrides, extra_dirs=operand.extra_dirs
        )
    if operand.source == "composed":
        path = (
            composed_config_path(reference, index=operand.config_index)
            if reference.is_dir()
            else reference
        )
        return compose_file(
            path, overrides=operand.overrides, extra_dirs=operand.extra_dirs
        )
    raise RefusalError(f"unknown comparison operand source {operand.source!r}")


def normalize_operands(
    operands: Sequence[ConfigOperand],
    *,
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    cwd: Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Normalize several operands while retaining per-operand overrides and search dirs."""
    return tuple(
        normalize_operand(item, compose_spec=compose_spec, project=project, cwd=cwd)
        for item in operands
    )


def _values_equal(left: Any, right: Any) -> bool:
    if left is ABSENT or right is ABSENT:
        return left is right
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    return bool(left == right)


def _values_differ(values: Sequence[Any]) -> bool:
    first = values[0]
    return any(not _values_equal(value, first) for value in values[1:])


def _validate_operand(
    value: Mapping[str, Any], *, root_type: type[BaseModel], label: str
) -> BaseModel:
    try:
        return root_type.model_validate(dict(value))
    except ValidationError as exc:
        raise RefusalError(
            f"comparison operand {label!r} failed {root_type.__name__} validation: {exc}"
        ) from None


def _project(
    value: Mapping[str, Any],
    *,
    level: ComparisonLevel,
    root_type: type[BaseModel] | None,
    validated: BaseModel | None = None,
) -> dict[str, Any]:
    if level == "literal":
        return deepcopy(dict(value))
    if root_type is None:
        raise RefusalError(f"comparison level {level!r} requires a root_type")
    dumped = (
        validated.model_dump(mode="json")
        if validated is not None
        else deepcopy(dict(value))
    )
    paths = (
        marked_field_paths(root_type, ResolvedOnlyMarker)
        if level == "authored"
        else excluded_source_paths(root_type)
    )
    for path in paths:
        prune_path(dumped, path)
    return dumped


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if not value:
            return {".".join(prefix): {}} if prefix else {}
        out: dict[str, Any] = {}
        for key, child in value.items():
            out.update(_flatten(child, (*prefix, str(key))))
        return out
    if isinstance(value, list):
        if not value:
            return {".".join(prefix): []} if prefix else {}
        out = {}
        for index, child in enumerate(value):
            out.update(_flatten(child, (*prefix, str(index))))
        return out
    return {".".join(prefix): value}


def _contains(include: str, path: str) -> bool:
    return path == include or path.startswith(f"{include}.")


def _hashes_for(path: str, registry: HashRegistry, command: str | None) -> tuple[str, ...]:
    moved: list[str] = []
    for declaration in registry.declarations:
        if not isinstance(declaration, FieldHash):
            continue
        include = declaration.include
        if isinstance(include, Mapping):
            includes = tuple(include.get(command, ())) if command is not None else ()
        else:
            includes = tuple(include)
        if any(_contains(item, path) for item in includes) and not any(
            _contains(item, path) for item in declaration.exclude
        ):
            moved.append(declaration.name)
    return tuple(moved)


def _matches_wildcard(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if len(parts) < len(pattern):
        return False
    return all(pat == WILDCARD or pat == seg for seg, pat in zip(parts, pattern))


def _is_excluded(path: str, root_type: type[BaseModel]) -> bool:
    parts = tuple(path.split("."))
    return any(
        _matches_wildcard(parts, excluded)
        for excluded in excluded_source_paths(root_type)
    )


def _diff_mappings(mappings: Sequence[Mapping[str, Any]]) -> tuple[Difference, ...]:
    flattened = [_flatten(item) for item in mappings]
    paths = sorted(set().union(*(item.keys() for item in flattened)))
    diffs: list[Difference] = []
    for path in paths:
        values = tuple(item.get(path, ABSENT) for item in flattened)
        if _values_differ(values):
            diffs.append(Difference(path=path, values=values))
    return tuple(diffs)


def _evaluate_pure_hashes(
    models: Sequence[BaseModel],
    *,
    hashes: HashRegistry,
    command: str | None,
) -> tuple[HashComparison, ...]:
    """Evaluate every ``reads_inputs=False`` declaration across validated operands."""
    for decl in hashes.declarations:
        if isinstance(decl, FieldHash) and isinstance(decl.include, Mapping):
            if command is None:
                raise RefusalError(
                    f"comparison level 'by_hash' requires command because "
                    f"FieldHash {decl.name!r} is command-scoped"
                )

    results = [
        hashes.compute(
            {"config": model, **({"command": command} if command is not None else {})},
            input_reading=False,
        )
        for model in models
    ]
    entry_ids = list(
        dict.fromkeys(eid for res in results for eid in res.entries)
    )

    comparisons: list[HashComparison] = []
    for eid in entry_ids:
        entries = tuple(res.entries.get(eid) for res in results)
        values = tuple(entry.value if entry is not None else None for entry in entries)
        equal = not _values_differ(values)
        sources = [
            entry.source if (entry is not None and isinstance(entry.source, Mapping)) else {}
            for entry in entries
        ]
        source_diffs = () if equal else _diff_mappings(sources)
        comparisons.append(
            HashComparison(
                name=eid,
                equal=equal,
                values=values,
                source_differences=source_diffs,
            )
        )
    return tuple(comparisons)


def compare_configs(
    operands: Sequence[Mapping[str, Any]],
    *,
    level: ComparisonLevel = "literal",
    labels: Sequence[str] | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    project: ProjectSpec | None = None,
) -> Comparison:
    """Compare two or more normalized configuration mappings.

    At ``authored``, ``behavioral``, and ``by_hash``, operands validate through
    ``root_type`` so schema defaults normalize consistently while ``literal``
    stays raw. At ``by_hash``, every pure (``reads_inputs=False``) declaration
    — ``FieldHash``, ``SourceHash``, and ``FannedHash`` — is evaluated directly
    for exact digests and source diffs, reserving ``opaque_hashes`` for
    input-reading (``reads_inputs=True``) declarations.
    """
    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    resolved_hashes = (
        hashes if hashes is not None else (project.hashes if project is not None else None)
    )

    if len(operands) < 2:
        raise RefusalError("configuration comparison needs at least two operands")
    if labels is None:
        labels = tuple(f"config {index + 1}" for index in range(len(operands)))
    if len(labels) != len(operands):
        raise RefusalError("comparison labels must have one entry per operand")
    if level != "literal" and resolved_root is None:
        raise RefusalError(f"comparison level {level!r} requires a root_type")
    if level == "by_hash" and resolved_hashes is None:
        raise RefusalError("comparison level 'by_hash' requires a hash registry")

    validated_models: tuple[BaseModel, ...] | None = None
    if level != "literal":
        assert resolved_root is not None
        validated_models = tuple(
            _validate_operand(item, root_type=resolved_root, label=label)
            for item, label in zip(operands, labels)
        )

    projection: ComparisonLevel = "behavioral" if level == "by_hash" else level
    projected = [
        _flatten(
            _project(
                item,
                level=projection,
                root_type=resolved_root,
                validated=validated_models[idx] if validated_models is not None else None,
            )
        )
        for idx, item in enumerate(operands)
    ]

    hash_comparisons: tuple[HashComparison, ...] = ()
    if level == "by_hash":
        assert validated_models is not None and resolved_hashes is not None
        hash_comparisons = _evaluate_pure_hashes(
            validated_models, hashes=resolved_hashes, command=command
        )

    # Map differing source paths on non-FieldHash pure hashes for attribution
    moved_by_source_path: dict[str, list[str]] = {}
    for hc in hash_comparisons:
        if not hc.equal:
            for sdiff in hc.source_differences:
                moved_by_source_path.setdefault(sdiff.path, []).append(hc.name)

    paths = sorted(set().union(*(item.keys() for item in projected)))
    differences: list[Difference] = []
    for path in paths:
        values = tuple(item.get(path, ABSENT) for item in projected)
        if not _values_differ(values):
            continue
        moved: list[str] = []
        if resolved_hashes is not None:
            moved.extend(_hashes_for(path, resolved_hashes, command))
            for hname in moved_by_source_path.get(path, ()):
                if hname not in moved:
                    moved.append(hname)
        differences.append(
            Difference(
                path=path,
                values=values,
                moved_hashes=tuple(moved),
                excluded=resolved_root is not None and _is_excluded(path, resolved_root),
            )
        )

    literal = [_flatten(dict(item)) for item in operands]
    literal_paths = sorted(set().union(*(item.keys() for item in literal)))
    diff_paths = {difference.path for difference in differences}
    residual = tuple(
        path
        for path in literal_paths
        if path not in diff_paths
        and _values_differ(tuple(item.get(path, ABSENT) for item in literal))
    )

    opaque: tuple[str, ...] = ()
    if resolved_hashes is not None:
        opaque = tuple(
            declaration.name
            for declaration in resolved_hashes.declarations
            if getattr(declaration, "reads_inputs", False)
        )

    return Comparison(
        level=level,
        labels=tuple(labels),
        differences=tuple(differences),
        residual_paths=residual,
        opaque_hashes=opaque,
        hash_comparisons=hash_comparisons,
    )


def compare_operands(
    operands: Sequence[ConfigOperand],
    *,
    compose_spec: ComposeSpec | None = None,
    level: ComparisonLevel = "literal",
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    project: ProjectSpec | None = None,
    cwd: Path | None = None,
) -> Comparison:
    """Normalize and compare declared configuration references in one call."""
    if level != "literal" and any(operand.fragment for operand in operands):
        raise RefusalError(
            "a configuration-group fragment is comparable only at the literal level"
        )
    labels = tuple(
        operand.label or f"config {index + 1}" for index, operand in enumerate(operands)
    )
    return compare_configs(
        normalize_operands(operands, compose_spec=compose_spec, project=project, cwd=cwd),
        level=level,
        labels=labels,
        root_type=root_type,
        hashes=hashes,
        command=command,
        project=project,
    )


def render_comparison(comparison: Comparison) -> str:
    """Render a compact plain-text report without losing structured data."""
    lines: list[str] = []
    if not comparison.differences:
        lines.append(f"No {comparison.level} differences.")
    else:
        lines.append(f"{comparison.level} differences:")
        for difference in comparison.differences:
            values = ", ".join(
                f"{label}={value!r}"
                for label, value in zip(comparison.labels, difference.values)
            )
            suffix = (
                f"; moves {', '.join(difference.moved_hashes)}"
                if difference.moved_hashes
                else ""
            )
            lines.append(f"  {difference.path}: {values}{suffix}")

    if comparison.hash_comparisons:
        lines.append("Hashes:")
        for hc in comparison.hash_comparisons:
            status = "equal" if hc.equal else "differs"
            lines.append(f"  {hc.name}: {status}")
            if not hc.equal:
                for label, digest in zip(comparison.labels, hc.values):
                    lines.append(f"    {label}: {digest}")
                for sdiff in hc.source_differences:
                    svals = ", ".join(
                        f"{label}={val!r}"
                        for label, val in zip(comparison.labels, sdiff.values)
                    )
                    lines.append(f"    source.{sdiff.path}: {svals}")

    if comparison.residual_paths:
        lines.append(f"Residual (literal-only): {', '.join(comparison.residual_paths)}")
    if comparison.opaque_hashes:
        lines.append(f"Opaque hashes: {', '.join(comparison.opaque_hashes)}")
    return "\n".join(lines)
