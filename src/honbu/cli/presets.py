"""Opt-in Typer option presets and mountable command builders (D19).

This is the only module in Honbu that imports Typer (enforced by
``test_layering``). Projects using Click, ``argparse``, or hand-written Typer
options never need to import it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

import typer

from honbu.cli.helpers import CONVENTIONAL_FLAGS, exit_on_refusal, validate_override_tokens
from honbu.core.hashes import HashRegistry
from honbu.errors import DeclarationError
from honbu.flow.compose import ComposeSpec
from honbu.flow.project import ProjectSpec
from pydantic import BaseModel

#: Click/Typer context settings letting Hydra overrides trail after typed options.
#: Unknown dash-prefixed tokens pass into ``ctx.args`` where
#: :func:`~honbu.cli.validate_override_tokens` refuses them cleanly instead of
#: forwarding them to Hydra.
HYDRA_CONTEXT_SETTINGS: dict[str, Any] = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


# --------------------------------------------------------------------------- #
# customizable option factories
# --------------------------------------------------------------------------- #


def config_option(
    *param_decls: str,
    help: str = "Compose this authored YAML file instead of the default search path.",
) -> Any:
    """Typer option factory for an authored configuration file."""
    decls = param_decls or (CONVENTIONAL_FLAGS["config"],)
    return typer.Option(*decls, help=help)


def resolved_option(
    *param_decls: str,
    help: str = "Replay a prior run's resolved configuration (run directory or resolved.yaml).",
) -> Any:
    """Typer option factory for a prior run's resolved configuration."""
    decls = param_decls or (CONVENTIONAL_FLAGS["resolved"],)
    return typer.Option(*decls, help=help)


def fork_from_option(
    *param_decls: str,
    help: str = "Fork a new run from a prior run's authored configuration with recorded lineage.",
) -> Any:
    """Typer option factory for forking from a parent run."""
    decls = param_decls or (CONVENTIONAL_FLAGS["fork_from"],)
    return typer.Option(*decls, help=help)


def fork_artifact_option(
    *param_decls: str,
    help: str = "Parent manifest artifact key to fork from (overrides command default).",
) -> Any:
    """Typer option factory for selecting the parent artifact key when forking."""
    decls = param_decls or (CONVENTIONAL_FLAGS["fork_artifact"],)
    return typer.Option(*decls, help=help)


def config_dirs_option(
    *param_decls: str,
    help: str = "Prepend an extra configuration search directory (repeatable).",
) -> Any:
    """Typer option factory for repeatable ``--config-dir`` search overlays."""
    decls = param_decls or (CONVENTIONAL_FLAGS["config_dir"],)
    return typer.Option(*decls, help=help)


def clobber_option(
    *param_decls: str,
    help: str = "Replace existing content within this command's declared write scope.",
) -> Any:
    """Typer option factory for scope-confined clobber permission."""
    decls = param_decls or (CONVENTIONAL_FLAGS["clobber"],)
    return typer.Option(*decls, help=help)


def resume_option(
    *param_decls: str,
    help: str = "Continue an unfinished run in place, skipping satisfied steps.",
) -> Any:
    """Typer option factory for resuming an unfinished run."""
    decls = param_decls or (CONVENTIONAL_FLAGS["resume"],)
    return typer.Option(*decls, help=help)


# --------------------------------------------------------------------------- #
# ready-made Annotated aliases
# --------------------------------------------------------------------------- #

ConfigOpt = Annotated[Path | None, config_option()]
ResolvedOpt = Annotated[Path | None, resolved_option()]
ForkFromOpt = Annotated[Path | None, fork_from_option()]
ForkArtifactOpt = Annotated[str | None, fork_artifact_option()]
ConfigDirsOpt = Annotated[list[Path] | None, config_dirs_option()]
ClobberOpt = Annotated[bool, clobber_option()]
ResumeOpt = Annotated[bool, resume_option()]


# --------------------------------------------------------------------------- #
# optional mountable Typer command builders
# --------------------------------------------------------------------------- #


def build_materialize_command(
    *,
    scopes: Mapping[str, Any],
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    defaults: Sequence[str] = (),
    default_destination: Path | str = Path("configs"),
    packaged_root: Path | None = None,
    prog: str | None = None,
) -> Callable[..., None]:
    """Build a mountable Typer command for config materialization (D27).

    Mount under whatever command name the project chooses::

        app.command("materialize")(build_materialize_command(project=PROJECT, scopes=SCOPES))
    """
    spec = compose_spec if compose_spec is not None else (project.compose_spec if project else None)
    if spec is None:
        raise DeclarationError("build_materialize_command requires compose_spec or project")

    default_dest = Path(default_destination)

    def materialize_cmd(
        scope: Annotated[
            list[str] | None,
            typer.Option("--scope", help="Materialization scope(s) to include (repeatable)."),
        ] = None,
        destination: Annotated[
            Path,
            typer.Option("--dest", "--destination", help="Target local config directory."),
        ] = default_dest,
        overwrite: Annotated[
            bool,
            typer.Option("--overwrite", help="Overwrite existing local config files."),
        ] = False,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Report planned actions without writing files."),
        ] = False,
    ) -> None:
        from honbu.materialize import (
            apply_materialization,
            plan_materialization,
            render_plan,
        )

        with exit_on_refusal(prog=prog):
            plan = plan_materialization(
                destination,
                compose_spec=spec,
                scopes=scopes,
                selected=tuple(scope or ()),
                defaults=tuple(defaults),
                overwrite=overwrite,
                packaged_root=packaged_root,
            )
            if not dry_run:
                apply_materialization(plan)
            typer.echo(render_plan(plan))

    return materialize_cmd


def build_compare_command(
    *,
    compose_spec: ComposeSpec | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    project: ProjectSpec | None = None,
    default_level: str = "behavioral",
    default_command: str | None = None,
    prog: str | None = None,
) -> Callable[..., None]:
    """Build a mountable Typer command for N-way config comparison (D26).

    Mount under whatever command name the project chooses::

        app.command("compare", context_settings=HYDRA_CONTEXT_SETTINGS)(
            build_compare_command(project=PROJECT)
        )
    """
    spec = compose_spec if compose_spec is not None else (project.compose_spec if project else None)
    root = root_type if root_type is not None else (project.root_type if project else None)
    reg = hashes if hashes is not None else (project.hashes if project else None)
    if spec is None:
        raise DeclarationError("build_compare_command requires compose_spec or project")

    def compare_cmd(
        ctx: typer.Context,
        left: Annotated[
            str,
            typer.Argument(help="First operand: 'default', or a YAML file / run directory path."),
        ],
        right: Annotated[
            str,
            typer.Argument(help="Second operand: 'default', or a YAML file / run directory path."),
        ],
        level: Annotated[
            str,
            typer.Option("--level", help="Comparison level: literal, authored, behavioral, by_hash."),
        ] = default_level,
        command: Annotated[
            str | None,
            typer.Option("--command", help="Command slug for command-scoped hashes at by_hash."),
        ] = default_command,
        composed: Annotated[
            bool,
            typer.Option("--composed", help="Read composed.yaml instead of resolved.yaml for run dirs."),
        ] = False,
    ) -> None:
        from honbu.compare import ConfigOperand, compare_operands, render_comparison

        def _to_operand(ref: str, overrides: tuple[str, ...] = ()) -> ConfigOperand:
            if ref == "default":
                return ConfigOperand(source="default", overrides=overrides, label="default")
            path = Path(ref)
            if path.is_dir():
                source = "composed" if composed else "resolved"
                return ConfigOperand(source=source, reference=path, overrides=overrides, label=ref)
            return ConfigOperand(source="file", reference=path, overrides=overrides, label=ref)

        with exit_on_refusal(prog=prog):
            overrides = validate_override_tokens(ctx.args)
            operands = (_to_operand(left), _to_operand(right, overrides=overrides))
            result = compare_operands(
                operands,
                compose_spec=spec,
                level=level,  # type: ignore[arg-type]
                root_type=root,
                hashes=reg,
                command=command,
            )
            typer.echo(render_comparison(result))

    return compare_cmd
