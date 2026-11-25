"""Plan and apply additive copies of packaged project configuration trees (D27).

Materialization is configs-only: it copies packaged configuration closures and
verbatim config-tree files (such as TOML styles), never fixture datasets. It is
planned, additive by default, explicit about overwrites, never deletes local
files, and answers divergence without a lockfile via two complementary drift
views: composed semantic drift (:func:`compare_materialized_config`) and
file-level tree inventory (:func:`materialized_file_drift`).
"""

from __future__ import annotations

import fnmatch
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from honbu.core.canonical import file_digest
from honbu.errors import DeclarationError, RefusalError
from honbu.flow.compose import (
    ComposeSpec,
    _packaged_dir,
    compose_file_with_choices,
    compose_with_choices,
)
from honbu.flow.project import ProjectSpec

if TYPE_CHECKING:
    from pydantic import BaseModel

    from honbu.compare import Comparison, ComparisonLevel
    from honbu.core.hashes import HashRegistry

MaterializationAction = Literal["create", "skip", "overwrite"]

#: Python package plumbing always skipped during directory walks and file drift scans.
DEFAULT_PLUMBING_EXCLUDES: tuple[str, ...] = (
    "__init__.py",
    "__pycache__",
    "*.pyc",
    "*.pyo",
)


@dataclass(frozen=True)
class MaterializationRoot:
    """A configuration root for transitive closure expansion.

    May name either a packaged configuration (``config_name`` with optional
    ``overrides``) or an external authored YAML file (:meth:`from_file`), in
    which case only the packaged group options it transitively selects are
    materialized while the authored file itself stays in place.
    """

    config_name: str | None = None
    overrides: tuple[str, ...] = ()
    file_path: Path | None = None

    def __post_init__(self) -> None:
        if (self.config_name is None) == (self.file_path is None):
            raise DeclarationError(
                "MaterializationRoot requires exactly one of config_name or file_path"
            )

    @classmethod
    def from_file(
        cls, path: Path | str, *, overrides: Sequence[str] = ()
    ) -> MaterializationRoot:
        """Build a root from an external authored YAML file outside the package."""
        return cls(file_path=Path(path), overrides=tuple(overrides))


@dataclass(frozen=True)
class MaterializationScope:
    """A project-named starter set over packaged configuration files.

    Configs only (D27):
    * ``roots`` — closure roots (:class:`MaterializationRoot` or packaged config names).
    * ``files`` — verbatim packaged-relative config files or directories (such as
      non-YAML TOML styles or whole config directories).
    * ``exclude`` — fnmatch globs skipped during directory walks in addition to
      Python package plumbing (``__init__.py``, ``__pycache__/``, ``*.pyc``, ``*.pyo``).
    """

    name: str
    roots: tuple[MaterializationRoot | str, ...] = ()
    files: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializedFile:
    source: Path
    destination: Path
    action: MaterializationAction


@dataclass(frozen=True)
class MaterializationPlan:
    destination: Path
    files: tuple[MaterializedFile, ...]

    @property
    def created(self) -> tuple[MaterializedFile, ...]:
        return tuple(item for item in self.files if item.action == "create")

    @property
    def skipped(self) -> tuple[MaterializedFile, ...]:
        return tuple(item for item in self.files if item.action == "skip")

    @property
    def overwritten(self) -> tuple[MaterializedFile, ...]:
        return tuple(item for item in self.files if item.action == "overwrite")


@dataclass(frozen=True)
class FileDrift:
    """File-level drift comparing a local config tree against packaged defaults."""

    local_root: Path
    packaged_root: Path
    modified: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    local_only: tuple[str, ...] = ()
    packaged_only: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.modified or self.local_only or self.packaged_only)


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise DeclarationError(
            f"materialization paths must be packaged-relative: {value!r}"
        )
    return path


def _is_excluded_relpath(relative: Path, extra_excludes: Sequence[str] = ()) -> bool:
    """Whether a relative config-tree path is package plumbing or excluded by glob."""
    posix = relative.as_posix()
    for part in relative.parts:
        if part == "__pycache__":
            return True
    name = relative.name
    if name == "__init__.py" or fnmatch.fnmatch(name, "*.pyc") or fnmatch.fnmatch(name, "*.pyo"):
        return True
    for pattern in extra_excludes:
        if fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in relative.parts):
            return True
    return False


def closure_roots(
    *,
    config_name: str | None,
    choices: Mapping[str, str],
    include_root: bool = True,
) -> tuple[str, ...]:
    """Turn Hydra's recorded selections into packaged configuration paths.

    The defaults-list algorithm is deliberately absent here. Hydra already made
    that decision; its ``runtime.choices`` records the transitive result. Internal
    ``hydra/*`` entries name framework machinery, not project files, and
    ``@package`` relocation suffixes (``group@pkg``) are stripped so options map
    to their actual packaged file paths.
    """
    roots: list[str] = []
    if include_root and config_name:
        roots.append(f"{config_name}.yaml")
    for group, option in choices.items():
        if group == "hydra" or group.startswith("hydra/") or option in {"null", "None", ""}:
            continue
        clean_group = group.split("@", 1)[0].strip("/")
        if not clean_group:
            continue
        roots.append((Path(clean_group) / f"{option}.yaml").as_posix())
    return tuple(dict.fromkeys(roots))


def closure_for_root(
    root: MaterializationRoot,
    *,
    compose_spec: ComposeSpec,
    cwd: Path | None = None,
) -> tuple[str, ...]:
    """Return one root's transitive packaged config closure.

    Asks Hydra for its selected options rather than re-parsing YAML defaults lists.
    For an authored file (:meth:`MaterializationRoot.from_file`), composes over
    ``[target.parent, local, packaged]`` and returns only the selected group
    options, so the authored file stays in place; options that exist only locally
    are dropped later by :func:`plan_materialization`.
    """
    if root.file_path is not None:
        # Search [target.parent, local, packaged]: an edited local group file (or a
        # child it pulls in) is honored transitively, so the closure names exactly
        # the packaged options that local edits now select. Entries resolving only
        # to local-only files are filtered by plan_materialization.
        composition = compose_file_with_choices(
            root.file_path,
            overrides=root.overrides,
            compose_spec=compose_spec,
            cwd=cwd,
        )
        return closure_roots(
            config_name=None, choices=composition.choices, include_root=False
        )

    assert root.config_name is not None
    packaged_only = replace(compose_spec, config_name=root.config_name)
    if compose_spec.packaged is not None:
        # Compose packaged-only without any local shadow (ComposeSpec.local=None).
        packaged_only = replace(packaged_only, local=None)
    composition = compose_with_choices(packaged_only, overrides=root.overrides, cwd=cwd)
    return closure_roots(
        config_name=root.config_name, choices=composition.choices, include_root=True
    )


def _resolve_packaged_root(
    *,
    compose_spec: ComposeSpec | None,
    packaged_root: Path | None,
    cwd: Path | None,
) -> Path:
    if packaged_root is not None:
        return Path(packaged_root).resolve()
    if compose_spec is not None and compose_spec.packaged is not None:
        return _packaged_dir(compose_spec.packaged)
    if compose_spec is not None and compose_spec.local is not None:
        base = (cwd or Path.cwd()).resolve()
        candidate = Path(compose_spec.local)
        return (candidate if candidate.is_absolute() else base / candidate).resolve()
    raise DeclarationError(
        "materialization requires compose_spec (or project) or an explicit packaged_root"
    )


def plan_materialization(
    destination: Path,
    *,
    scopes: Mapping[str, MaterializationScope],
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    selected: Sequence[str] = (),
    defaults: Sequence[str] = (),
    overwrite: bool = False,
    packaged_root: Path | None = None,
    cwd: Path | None = None,
) -> MaterializationPlan:
    """Plan materialization for declared scopes without touching the destination.

    Derives the packaged root automatically from ``compose_spec`` (or ``project``),
    with ``packaged_root`` available as an override for unpacked test trees.
    Expands every closure root through Hydra's recorded choices, walks verbatim
    config-tree ``files`` skipping Python package plumbing and scope ``exclude``
    globs, and classifies each file as ``create``, ``skip``, or ``overwrite``.
    """
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    pkg_root = _resolve_packaged_root(
        compose_spec=spec, packaged_root=packaged_root, cwd=cwd
    )
    effective_spec = (
        spec
        if spec is not None
        else ComposeSpec(packaged=None, local=str(pkg_root))
    )

    names = tuple(selected or defaults)
    if not names:
        raise RefusalError(
            "no materialization scope selected and the project declares no defaults"
        )

    files: dict[Path, Path] = {}
    for name in names:
        scope = scopes.get(name)
        if scope is None:
            raise RefusalError(
                f"unknown materialization scope {name!r}; known: {', '.join(scopes)}"
            )
        for root_entry in scope.roots:
            root_obj = (
                root_entry
                if isinstance(root_entry, MaterializationRoot)
                else MaterializationRoot(root_entry.removesuffix(".yaml"))
            )
            for rel_str in closure_for_root(
                root_obj, compose_spec=effective_spec, cwd=cwd
            ):
                relative = _safe_relative(rel_str)
                if _is_excluded_relpath(relative, scope.exclude):
                    continue
                source = pkg_root / relative
                if not source.is_file():
                    if root_obj.file_path is not None:
                        # A local-only option the user authored: nothing packaged
                        # to copy, and nothing wrong with it.
                        continue
                    raise DeclarationError(
                        f"scope {name!r} closure names no packaged file: {relative}"
                    )
                files[relative] = source

        for entry in scope.files:
            relative = _safe_relative(entry)
            source = pkg_root / relative
            if not source.exists():
                raise DeclarationError(
                    f"scope {name!r} names no packaged file or directory: {relative}"
                )
            candidates = sorted(source.rglob("*")) if source.is_dir() else (source,)
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                rel = candidate.relative_to(pkg_root)
                if _is_excluded_relpath(rel, scope.exclude):
                    continue
                files[rel] = candidate

    dest = Path(destination)
    planned: list[MaterializedFile] = []
    for relative, source in sorted(files.items()):
        target = dest / relative
        action: MaterializationAction = (
            "overwrite"
            if target.exists() and overwrite
            else "skip"
            if target.exists()
            else "create"
        )
        planned.append(MaterializedFile(source=source, destination=target, action=action))
    return MaterializationPlan(destination=dest, files=tuple(planned))


def apply_materialization(plan: MaterializationPlan) -> MaterializationPlan:
    """Apply a prior plan additively; no operation ever deletes a local file."""
    for item in plan.files:
        if item.action == "skip":
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.destination)
    return plan


def render_plan(plan: MaterializationPlan) -> str:
    """Render a plain-text summary of a materialization plan."""
    if not plan.files:
        return f"Materialization plan for {plan.destination}: nothing to do."
    summary = (
        f"Materialization plan for {plan.destination}: "
        f"{len(plan.created)} create, {len(plan.skipped)} skip, "
        f"{len(plan.overwritten)} overwrite"
    )
    lines = [summary]
    for item in plan.files:
        try:
            rel = item.destination.relative_to(plan.destination).as_posix()
        except ValueError:
            rel = str(item.destination)
        lines.append(f"  {item.action:<9} {rel}")
    return "\n".join(lines)


def compare_materialized_config(
    local_root: Path,
    *,
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    config_name: str | None = None,
    overrides: Sequence[str] = (),
    level: ComparisonLevel = "literal",
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    cwd: Path | None = None,
) -> Comparison:
    """Compare packaged-only defaults against effective local-shadowing-packaged config.

    Because the local side falls through to packaged defaults for any omitted
    group, partial local trees compare cleanly — even when the local tree omits
    the primary ``config.yaml`` and overrides only one group file.
    """
    from honbu.compare import compare_configs

    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    if spec is None or spec.packaged is None:
        raise DeclarationError("drift comparison requires a packaged configuration module")

    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    resolved_hashes = (
        hashes if hashes is not None else (project.hashes if project is not None else None)
    )

    name = config_name or spec.config_name
    packaged = compose_with_choices(
        replace(spec, local=None, config_name=name),
        overrides=overrides,
        cwd=cwd,
    ).config

    local_path = Path(local_root).resolve()
    if (local_path / f"{name}.yaml").is_file():
        local = compose_with_choices(
            ComposeSpec(packaged=spec.packaged, local=str(local_path), config_name=name),
            overrides=overrides,
            cwd=cwd,
        ).config
    else:
        # Partial local tree lacking primary config.yaml: stage only the packaged
        # primary file in a temp dir as primary so Hydra's searchpath searches
        # [local_path, packaged] in order, letting local group files shadow packaged.
        pkg_root = _packaged_dir(spec.packaged)
        primary_file = pkg_root / f"{name}.yaml"
        if not primary_file.is_file():
            raise RefusalError(f"packaged defaults hold no {name}.yaml in {pkg_root}")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            shutil.copy2(primary_file, tmp_dir / f"{name}.yaml")
            local = compose_with_choices(
                ComposeSpec(
                    packaged=spec.packaged,
                    local=str(local_path) if local_path.is_dir() else None,
                    config_name=name,
                ),
                overrides=overrides,
                extra_dirs=(tmp_dir,),
                cwd=cwd,
            ).config

    return compare_configs(
        (packaged, local),
        labels=("packaged", "local"),
        level=level,
        root_type=resolved_root,
        hashes=resolved_hashes,
        command=command,
    )


def materialized_file_drift(
    local_root: Path,
    *,
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    packaged_root: Path | None = None,
    exclude: Sequence[str] = (),
    cwd: Path | None = None,
) -> FileDrift:
    """Compare files in a local config tree against packaged defaults (no lockfile).

    Skips Python package plumbing and any ``exclude`` globs, classifying each
    relative path as ``modified``, ``unchanged``, ``local_only``, or ``packaged_only``.
    """
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    pkg_root = _resolve_packaged_root(
        compose_spec=spec, packaged_root=packaged_root, cwd=cwd
    )
    loc_root = Path(local_root).resolve()

    def _scan(root: Path) -> dict[str, Path]:
        if not root.is_dir():
            return {}
        found: dict[str, Path] = {}
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(root)
            if _is_excluded_relpath(rel, exclude):
                continue
            found[rel.as_posix()] = candidate
        return found

    pkg_files = _scan(pkg_root)
    loc_files = _scan(loc_root)

    modified: list[str] = []
    unchanged: list[str] = []
    local_only: list[str] = []
    packaged_only: list[str] = []

    for rel in sorted(set(pkg_files) | set(loc_files)):
        in_pkg = rel in pkg_files
        in_loc = rel in loc_files
        if in_pkg and in_loc:
            if file_digest(pkg_files[rel]) == file_digest(loc_files[rel]):
                unchanged.append(rel)
            else:
                modified.append(rel)
        elif in_loc:
            local_only.append(rel)
        else:
            packaged_only.append(rel)

    return FileDrift(
        local_root=loc_root,
        packaged_root=pkg_root,
        modified=tuple(modified),
        unchanged=tuple(unchanged),
        local_only=tuple(local_only),
        packaged_only=tuple(packaged_only),
    )


def render_file_drift(drift: FileDrift) -> str:
    """Render a plain-text file-level drift report."""
    if not drift.has_drift:
        return f"No file drift ({len(drift.unchanged)} unchanged)."
    lines = [
        f"File drift ({drift.local_root} vs {drift.packaged_root}): "
        f"{len(drift.modified)} modified, {len(drift.local_only)} local-only, "
        f"{len(drift.packaged_only)} packaged-only, {len(drift.unchanged)} unchanged"
    ]
    for path in drift.modified:
        lines.append(f"  modified       {path}")
    for path in drift.local_only:
        lines.append(f"  local_only     {path}")
    for path in drift.packaged_only:
        lines.append(f"  packaged_only  {path}")
    return "\n".join(lines)
