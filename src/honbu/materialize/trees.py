"""Plan and apply additive copies of packaged project starter trees."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
from dataclasses import replace
from typing import TYPE_CHECKING

from honbu.errors import DeclarationError, RefusalError
from honbu.flow.compose import ComposeSpec, compose_with_choices

if TYPE_CHECKING:
    from pydantic import BaseModel

    from honbu.compare import Comparison, ComparisonLevel
    from honbu.core.hashes import HashRegistry


@dataclass(frozen=True)
class MaterializationRoot:
    """A packaged configuration root and the overrides used to select it."""

    config_name: str
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializationScope:
    """A project-named starter set rooted in packaged files."""

    name: str
    roots: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    config_roots: tuple[MaterializationRoot, ...] = ()


@dataclass(frozen=True)
class MaterializedFile:
    source: Path
    destination: Path
    action: str


@dataclass(frozen=True)
class MaterializationPlan:
    destination: Path
    files: tuple[MaterializedFile, ...]

    @property
    def created(self) -> tuple[MaterializedFile, ...]:
        return tuple(item for item in self.files if item.action == "create")


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise DeclarationError(f"materialization paths must be packaged-relative: {value!r}")
    return path


def closure_roots(
    *, config_name: str, choices: Mapping[str, str]
) -> tuple[str, ...]:
    """Turn Hydra's recorded selections into packaged configuration paths.

    The defaults-list algorithm is deliberately absent here. Hydra already made
    that decision; its ``runtime.choices`` records the transitive result. Internal
    ``hydra/*`` entries name framework machinery, not project files.
    """
    roots = [f"{config_name}.yaml"]
    for group, option in choices.items():
        if group.startswith("hydra/") or option in {"null", "None"}:
            continue
        roots.append(str(Path(group) / f"{option}.yaml"))
    return tuple(dict.fromkeys(roots))


def closure_for_root(
    root: MaterializationRoot,
    *,
    compose_spec: ComposeSpec,
    cwd: Path | None = None,
) -> tuple[str, ...]:
    """Return one packaged root's transitive config closure.

    This intentionally asks Hydra for its selected options. It does not inspect
    YAML defaults lists, which would duplicate and eventually diverge from the
    compositor's selection rules.
    """
    packaged_only = replace(compose_spec, config_name=root.config_name)
    if compose_spec.packaged is not None:
        # A materialization plan must describe the shipped tree, not an existing
        # local shadow. A project testing from an unpacked tree may instead pass
        # that tree as ``local`` with no packaged module.
        packaged_only = replace(packaged_only, local=".honbu-no-local-configs")
    composition = compose_with_choices(packaged_only, overrides=root.overrides, cwd=cwd)
    return closure_roots(config_name=root.config_name, choices=composition.choices)


def plan_config_materialization(
    packaged_root: Path,
    destination: Path,
    *,
    compose_spec: ComposeSpec,
    scopes: Mapping[str, MaterializationScope],
    selected: Sequence[str] = (),
    defaults: Sequence[str] = (),
    overwrite: bool = False,
    cwd: Path | None = None,
) -> MaterializationPlan:
    """Plan declared scopes, expanding their config roots through Hydra choices."""
    expanded: dict[str, MaterializationScope] = {}
    for name, scope in scopes.items():
        roots = list(scope.roots)
        for root in scope.config_roots:
            roots.extend(closure_for_root(root, compose_spec=compose_spec, cwd=cwd))
        expanded[name] = replace(scope, roots=tuple(dict.fromkeys(roots)))
    return plan_materialization(
        packaged_root,
        destination,
        scopes=expanded,
        selected=selected,
        defaults=defaults,
        overwrite=overwrite,
    )


def plan_materialization(
    packaged_root: Path,
    destination: Path,
    *,
    scopes: Mapping[str, MaterializationScope],
    selected: Sequence[str] = (),
    defaults: Sequence[str] = (),
    overwrite: bool = False,
) -> MaterializationPlan:
    """Classify project-declared packaged files without touching the destination."""
    names = tuple(selected or defaults)
    if not names:
        raise RefusalError("no materialization scope selected and the project declares no defaults")
    files: dict[Path, Path] = {}
    for name in names:
        scope = scopes.get(name)
        if scope is None:
            raise RefusalError(f"unknown materialization scope {name!r}; known: {', '.join(scopes)}")
        for entry in (*scope.roots, *scope.resources):
            relative = _safe_relative(entry)
            source = packaged_root / relative
            if not source.exists():
                raise DeclarationError(f"scope {name!r} names no packaged resource: {relative}")
            candidates = source.rglob("*") if source.is_dir() else (source,)
            for candidate in candidates:
                if candidate.is_file():
                    files[candidate.relative_to(packaged_root)] = candidate
    planned = []
    for relative, source in sorted(files.items()):
        target = destination / relative
        action = "overwrite" if target.exists() and overwrite else "skip" if target.exists() else "create"
        planned.append(MaterializedFile(source=source, destination=target, action=action))
    return MaterializationPlan(destination=destination, files=tuple(planned))


def apply_materialization(plan: MaterializationPlan) -> MaterializationPlan:
    """Apply a prior plan additively; no operation ever deletes a local file."""
    for item in plan.files:
        if item.action == "skip":
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.destination)
    return plan


def compare_materialized_config(
    local_root: Path,
    *,
    compose_spec: ComposeSpec,
    config_name: str | None = None,
    level: "ComparisonLevel" = "literal",
    root_type: type["BaseModel"] | None = None,
    hashes: "HashRegistry" | None = None,
    command: str | None = None,
    cwd: Path | None = None,
) -> "Comparison":
    """Compare a materialized root against the packaged configuration it mirrors."""
    from honbu.compare import compare_configs

    if compose_spec.packaged is None:
        raise DeclarationError("drift comparison requires a packaged configuration module")
    name = config_name or compose_spec.config_name
    packaged = compose_with_choices(
        replace(compose_spec, local=".honbu-no-local-configs", config_name=name), cwd=cwd
    ).config
    local = compose_with_choices(
        ComposeSpec(packaged=None, local=str(local_root), config_name=name), cwd=cwd
    ).config
    return compare_configs(
        (packaged, local),
        labels=("packaged", "local"),
        level=level,
        root_type=root_type,
        hashes=hashes,
        command=command,
    )
