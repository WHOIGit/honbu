"""Typed configuration loading and fork lineage helpers (D15, D17, D19).

CLI-free counterparts to the removed ``harness`` dispatcher: projects own their
CLI library, parse their own flags, and pass typed Python values here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.core.blocks import CONFIG_HASH_NAME
from honbu.core.io import read_json
from honbu.core.targets import MANIFEST_NAME
from honbu.errors import DeclarationError, RefusalError
from honbu.flow.commands import CommandSpec
from honbu.flow.compose import (
    ComposeSpec,
    ConfigSource,
    compose_config,
    compose_file,
    prior_config_path,
)
from honbu.flow.project import ProjectSpec
from honbu.flow.resolve import strip_resolved_only
from honbu.flow.run import COMPOSED_NAME, RESOLVED_NAME


@dataclass(frozen=True)
class LoadedConfig:
    """A loaded configuration mapping plus provenance metadata for resolution."""

    composed: dict[str, Any]
    source: ConfigSource | None = None
    prior_run: Path | None = None
    replay: bool = False
    overrides: tuple[str, ...] = ()
    config_index: int = 1


def load_config(
    source: ConfigSource | None = "default",
    reference: Path | str | None = None,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    compose_spec: ComposeSpec | None = None,
    root_type: type[BaseModel] | None = None,
    project: ProjectSpec | None = None,
    command: CommandSpec | None = None,
    config_index: int = 1,
    cwd: Path | None = None,
) -> LoadedConfig:
    """Load whichever configuration source was selected, with zero CLI coupling.

    Accepts ``default``, ``file``, ``resolved``, or ``fork`` (or ``None`` for a
    command declaring no configuration sources). Enforces ``command.config_sources``
    when ``command`` is supplied.
    """
    spec = compose_spec if compose_spec is not None else (project.compose_spec if project else None)
    root = root_type if root_type is not None else (project.root_type if project else None)
    override_tuple = tuple(overrides)

    if command is not None:
        if not command.config_sources:
            if source is not None:
                raise RefusalError(
                    f"{command.invocation} takes no configuration; got source {source!r}"
                )
            return LoadedConfig(composed={}, source=None)
        if source is None or source not in command.config_sources:
            accepted = ", ".join(command.config_sources)
            raise RefusalError(
                f"{command.invocation} does not accept configuration source {source!r}; "
                f"accepted: {accepted}"
            )

    match source:
        case None:
            return LoadedConfig(composed={}, source=None)
        case "default":
            if reference is not None:
                raise RefusalError("configuration source 'default' takes no reference")
            if spec is None:
                raise DeclarationError(
                    "load_config('default') requires compose_spec or project"
                )
            composed = compose_config(
                spec, overrides=override_tuple, extra_dirs=extra_dirs, cwd=cwd
            )
            return LoadedConfig(
                composed=composed,
                source="default",
                overrides=override_tuple,
            )
        case "file":
            if reference is None:
                raise RefusalError("configuration source 'file' requires a file path")
            composed = compose_file(
                reference, overrides=override_tuple, extra_dirs=extra_dirs
            )
            return LoadedConfig(
                composed=composed,
                source="file",
                overrides=override_tuple,
            )
        case "resolved":
            if reference is None:
                raise RefusalError(
                    "configuration source 'resolved' requires a run directory or resolved.yaml"
                )
            located, run_dir = prior_config_path(
                reference, RESOLVED_NAME, index=config_index
            )
            composed = compose_file(
                located, overrides=override_tuple, extra_dirs=extra_dirs
            )
            return LoadedConfig(
                composed=composed,
                source="resolved",
                prior_run=run_dir,
                replay=True,
                overrides=override_tuple,
                config_index=config_index,
            )
        case "fork":
            if reference is None:
                raise RefusalError(
                    "configuration source 'fork' requires a parent run directory or composed.yaml"
                )
            if root is None:
                raise DeclarationError("load_config('fork') requires root_type or project")
            located, run_dir = prior_config_path(
                reference, COMPOSED_NAME, index=config_index
            )
            composed = compose_file(
                located, overrides=override_tuple, extra_dirs=extra_dirs
            )
            return LoadedConfig(
                composed=strip_resolved_only(composed, root),
                source="fork",
                prior_run=run_dir,
                replay=False,
                overrides=override_tuple,
                config_index=config_index,
            )

    raise RefusalError(f"unknown configuration source {source!r}")


def parent_record(
    run_dir: Path | str,
    *,
    artifact: str | None = None,
    command: CommandSpec | None = None,
    config_hash_name: str | None = None,
    project: ProjectSpec | None = None,
) -> dict[str, str]:
    """Read verified parent lineage from a prior run's manifest for forking (D15).

    The artifact key is caller-selectable via ``artifact=...``, falling back to
    ``command.fork_artifact`` when omitted. Returns a mapping holding ``run_dir``,
    ``config_hash``, ``artifact_key``, and ``artifact`` (stored path relative to
    the parent run or absolute), which can be inspected before ``resolve()`` and
    passed directly to ``open_run(..., parent=...)``.
    """
    run_path = Path(run_dir).resolve()
    manifest_path = run_path / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RefusalError(f"{run_path} has no {MANIFEST_NAME} to fork from")
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError):
        raise RefusalError(f"{run_path} has no readable {MANIFEST_NAME} to fork from") from None
    if not isinstance(manifest, dict):
        raise RefusalError(f"{run_path} has no readable {MANIFEST_NAME} to fork from")

    artifact_key = (
        artifact
        if artifact is not None
        else (command.fork_artifact if command is not None else None)
    )
    if not artifact_key:
        raise RefusalError(
            "forking requires a parent artifact key; supply artifact=... or set "
            "CommandSpec.fork_artifact"
        )

    artifacts = manifest.get("artifacts")
    stored = artifacts.get(artifact_key) if isinstance(artifacts, Mapping) else None
    if stored is None:
        available = (
            ", ".join(sorted(artifacts)) if isinstance(artifacts, Mapping) and artifacts else "(none)"
        )
        raise RefusalError(
            f"{run_path} registered no {artifact_key!r} artifact to fork from; "
            f"it has: {available}"
        )

    hash_name = (
        config_hash_name
        if config_hash_name is not None
        else (project.config_hash_name if project is not None else CONFIG_HASH_NAME)
    )
    digests = manifest.get("hashes") or {}
    recorded = digests.get(hash_name) if isinstance(digests, Mapping) else None
    config_hash = (
        str(recorded.get("value") or "") if isinstance(recorded, Mapping) else ""
    )

    return {
        "run_dir": str(run_path),
        "config_hash": config_hash,
        "artifact_key": artifact_key,
        "artifact": str(stored),
    }
