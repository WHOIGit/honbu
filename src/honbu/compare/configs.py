"""Schema-aware, N-way configuration comparison.

The comparison engine works on mappings so callers decide how operands are
obtained (composition, a saved run, or a file).  A root schema unlocks the
marker-defined projections; a hash registry additionally explains which
declarative identities a behavioural difference would move.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from honbu.core.hashes import FieldHash, HashRegistry
from honbu.flow.compose import (
    ComposeSpec,
    composed_config_path,
    compose_config,
    compose_file,
    resolved_config_path,
)
from honbu.core.markers import (
    ResolvedOnlyMarker,
    excluded_source_paths,
    marked_field_paths,
    parse_dotted,
    prune_path,
)
from honbu.errors import RefusalError

ComparisonLevel = Literal["literal", "authored", "behavioral", "by_hash"]
OperandSource = Literal["default", "file", "resolved", "continue", "run"]


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
class Comparison:
    """A structured comparison suitable for project-specific rendering."""

    level: ComparisonLevel
    labels: tuple[str, ...]
    differences: tuple[Difference, ...]
    residual_paths: tuple[str, ...] = ()
    opaque_hashes: tuple[str, ...] = ()


def normalize_operand(
    operand: ConfigOperand,
    *,
    compose_spec: ComposeSpec,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Compose one declared operand to the mapping comparison operates on.

    The source names mirror Honbu's command sources. A directory accepted for a
    prior source is a run directory, so callers never need to know the provenance
    bundle's internal filenames.
    """
    if operand.source == "default":
        if operand.reference is not None:
            raise RefusalError("a default comparison operand takes no reference")
        return compose_config(compose_spec, overrides=operand.overrides, cwd=cwd)
    if operand.reference is None:
        raise RefusalError(f"comparison operand source {operand.source!r} needs a reference")

    reference = Path(operand.reference)
    if operand.source == "file":
        return compose_file(reference, overrides=operand.overrides)
    if operand.source == "resolved":
        path = resolved_config_path(reference) if reference.is_dir() else reference
        return compose_file(path, overrides=operand.overrides)
    if operand.source == "continue":
        path = composed_config_path(reference) if reference.is_dir() else reference
        return compose_file(path, overrides=operand.overrides)
    if operand.source == "run":
        if not reference.is_dir():
            raise RefusalError("a run comparison operand must name a run directory")
        return compose_file(resolved_config_path(reference), overrides=operand.overrides)
    raise RefusalError(f"unknown comparison operand source {operand.source!r}")


def normalize_operands(
    operands: Sequence[ConfigOperand],
    *,
    compose_spec: ComposeSpec,
    cwd: Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Normalize several operands while retaining per-operand overrides."""
    return tuple(normalize_operand(item, compose_spec=compose_spec, cwd=cwd) for item in operands)


def _project(
    value: Mapping[str, Any], *, level: ComparisonLevel, root_type: type[BaseModel] | None
) -> dict[str, Any]:
    result = deepcopy(dict(value))
    if level == "literal":
        return result
    if root_type is None:
        raise RefusalError(f"comparison level {level!r} requires a root_type")
    paths = (
        marked_field_paths(root_type, ResolvedOnlyMarker)
        if level == "authored"
        else excluded_source_paths(root_type)
    )
    for path in paths:
        prune_path(result, path)
    return result


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            out.update(_flatten(child, (*prefix, str(key))))
        return out
    if isinstance(value, list):
        out: dict[str, Any] = {}
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


def _is_excluded(path: str, root_type: type[BaseModel]) -> bool:
    parts = parse_dotted(path, allow_index=True)
    for excluded in excluded_source_paths(root_type):
        concrete = tuple(part for part in excluded if part != "*")
        if parts[: len(concrete)] == concrete:
            return True
    return False


def compare_configs(
    operands: Sequence[Mapping[str, Any]],
    *,
    level: ComparisonLevel = "literal",
    labels: Sequence[str] | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
) -> Comparison:
    """Compare two or more normalized configuration mappings.

    ``by_hash`` has behavioral projection semantics and adds declarative hash
    attribution. Callable and byte-backed declarations remain deliberately
    opaque: comparison cannot discover their runtime inputs safely.
    """
    if len(operands) < 2:
        raise RefusalError("configuration comparison needs at least two operands")
    if labels is None:
        labels = tuple(f"config {index + 1}" for index in range(len(operands)))
    if len(labels) != len(operands):
        raise RefusalError("comparison labels must have one entry per operand")
    if level == "by_hash" and hashes is None:
        raise RefusalError("comparison level 'by_hash' requires a hash registry")
    projection = "behavioral" if level == "by_hash" else level
    projected = [_flatten(_project(item, level=projection, root_type=root_type)) for item in operands]
    paths = sorted(set().union(*(item.keys() for item in projected)))
    differences: list[Difference] = []
    for path in paths:
        values = tuple(item.get(path, ABSENT) for item in projected)
        if all(value == values[0] for value in values[1:]):
            continue
        differences.append(
            Difference(
                path=path,
                values=values,
                moved_hashes=_hashes_for(path, hashes, command) if hashes is not None else (),
                excluded=root_type is not None and _is_excluded(path, root_type),
            )
        )
    literal = [_flatten(dict(item)) for item in operands]
    literal_paths = set().union(*(item.keys() for item in literal))
    residual = tuple(
        path
        for path in sorted(literal_paths)
        if len({repr(item.get(path, ABSENT)) for item in literal}) > 1
        and path not in {difference.path for difference in differences}
    )
    opaque = ()
    if hashes is not None:
        opaque = tuple(
            declaration.name
            for declaration in hashes.declarations
            if not isinstance(declaration, FieldHash)
        )
    return Comparison(
        level=level,
        labels=tuple(labels),
        differences=tuple(differences),
        residual_paths=residual,
        opaque_hashes=opaque,
    )


def compare_operands(
    operands: Sequence[ConfigOperand],
    *,
    compose_spec: ComposeSpec,
    level: ComparisonLevel = "literal",
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    cwd: Path | None = None,
) -> Comparison:
    """Normalize and compare declared configuration references in one call."""
    if level != "literal" and any(operand.fragment for operand in operands):
        raise RefusalError("a configuration-group fragment is comparable only at the literal level")
    labels = tuple(
        operand.label or f"config {index + 1}" for index, operand in enumerate(operands)
    )
    return compare_configs(
        normalize_operands(operands, compose_spec=compose_spec, cwd=cwd),
        level=level,
        labels=labels,
        root_type=root_type,
        hashes=hashes,
        command=command,
    )


def render_comparison(comparison: Comparison) -> str:
    """Render a compact plain-text report without losing structured data."""
    if not comparison.differences:
        return f"No {comparison.level} differences."
    lines = [f"{comparison.level} differences:"]
    for difference in comparison.differences:
        values = ", ".join(
            f"{label}={value!r}" for label, value in zip(comparison.labels, difference.values)
        )
        suffix = f"; moves {', '.join(difference.moved_hashes)}" if difference.moved_hashes else ""
        lines.append(f"  {difference.path}: {values}{suffix}")
    if comparison.residual_paths:
        lines.append(f"Residual (literal-only): {', '.join(comparison.residual_paths)}")
    if comparison.opaque_hashes:
        lines.append(f"Opaque hashes: {', '.join(comparison.opaque_hashes)}")
    return "\n".join(lines)
