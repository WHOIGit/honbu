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

from honbu.cli.helpers import (
    CONVENTIONAL_FLAGS,
    exit_on_refusal,
    split_dash_rule,
    validate_override_tokens,
)
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
    """Typer option factory for an authored configuration file (``-c`` / ``--config``)."""
    decls = param_decls or ("-c", CONVENTIONAL_FLAGS["config"])
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

    Flags: ``--scope`` (repeatable), ``--from FILE`` (plan the closure of an
    authored file, pulling in only newly referenced packaged options), ``--dest``,
    ``--clobber`` (overwrite existing local files; spelled like
    ``CONVENTIONAL_FLAGS["clobber"]``), and ``--dry-run``.

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
        from_file: Annotated[
            Path | None,
            typer.Option(
                "--from",
                help=(
                    "Copy only the packaged options this authored YAML transitively "
                    "selects (searching its directory, the local tree, then packaged "
                    "defaults). Local-only options are skipped."
                ),
            ),
        ] = None,
        destination: Annotated[
            Path,
            typer.Option("--dest", "--destination", help="Target local config directory."),
        ] = default_dest,
        clobber: Annotated[
            bool,
            clobber_option(help="Overwrite existing local config files."),
        ] = False,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Report planned actions without writing files."),
        ] = False,
    ) -> None:
        from honbu.materialize import (
            MaterializationRoot,
            MaterializationScope,
            apply_materialization,
            plan_materialization,
            render_plan,
        )

        with exit_on_refusal(prog=prog):
            selected = list(scope or ())
            all_scopes: dict[str, Any] = dict(scopes)
            if from_file is not None:
                # An ad-hoc scope holding just this root, under a name no project
                # scope can collide with. Without --scope it replaces the defaults.
                from_name = "--from"
                all_scopes[from_name] = MaterializationScope(
                    from_name, roots=(MaterializationRoot.from_file(from_file),)
                )
                selected.append(from_name)
            plan = plan_materialization(
                destination,
                compose_spec=spec,
                scopes=all_scopes,
                selected=tuple(selected),
                defaults=tuple(defaults),
                overwrite=clobber,
                packaged_root=packaged_root,
            )
            if not dry_run:
                apply_materialization(plan)
            typer.echo(render_plan(plan))

    return materialize_cmd


#: Operand designators accepted by :func:`build_compare_command`. The ``@`` keeps
#: them from colliding with a configuration file named ``default.yaml``.
DEFAULT_OPERAND = "@default"
PACKAGED_OPERAND = "@packaged"


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

    Takes two or more operands, each one of:

    * ``@default`` — the project's default root (local shadowing packaged);
    * ``@packaged`` / ``@packaged:<name>`` — shipped defaults only, ignoring
      ``./configs``, optionally a named packaged root;
    * a run directory — its ``resolved.yaml`` (or ``composed.yaml`` with
      ``--composed``);
    * a configuration name or path, resolved forgivingly by
      :func:`~honbu.cli.resolve_positional_config`.

    ``key=value`` overrides apply to the operand they follow, so
    ``compare @default @default epochs=50`` reports what the override changes.
    ``--config-dir`` overlays apply to ``@default`` and configuration files.

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
        operands: Annotated[
            list[str],
            typer.Argument(
                help=(
                    "Two or more operands: @default, @packaged, @packaged:<name>, a run "
                    "directory, or a config name/path; key=value overrides apply to "
                    "the operand they follow."
                ),
                show_default=False,
            ),
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
        config_dir: ConfigDirsOpt = None,
    ) -> None:
        from honbu.cli.helpers import _is_run_dir, resolve_positional_config
        from honbu.compare import ConfigOperand, compare_operands, render_comparison
        from honbu.errors import RefusalError

        extra = tuple(config_dir or ())

        def _to_operand(ref: str, overrides: tuple[str, ...]) -> ConfigOperand:
            if ref == DEFAULT_OPERAND:
                return ConfigOperand(
                    source="default", overrides=overrides, extra_dirs=extra, label=ref
                )
            if ref == PACKAGED_OPERAND or ref.startswith(f"{PACKAGED_OPERAND}:"):
                raw_name = ref.partition(":")[2] if ":" in ref else None
                name = raw_name.removesuffix(".yaml") if raw_name is not None else None
                if name is not None and not name.strip():
                    raise RefusalError(
                        f"{ref!r} names no packaged root; use {PACKAGED_OPERAND}:<name>"
                    )
                return ConfigOperand(
                    source="packaged", config_name=name, overrides=overrides, label=ref
                )
            if ref.startswith("@"):
                raise RefusalError(
                    f"unknown operand designator {ref!r}; use {DEFAULT_OPERAND}, "
                    f"{PACKAGED_OPERAND}, or {PACKAGED_OPERAND}:<name>"
                )
            path = Path(ref)
            if _is_run_dir(path.resolve()):
                return ConfigOperand(
                    source="composed" if composed else "resolved",
                    reference=path,
                    overrides=overrides,
                    label=ref,
                )
            try:
                located = resolve_positional_config(
                    ref, compose_spec=spec, extra_dirs=extra, reject_run_dir=False
                )
            except RefusalError as exc:
                if ref in {"default", "packaged"}:
                    raise RefusalError(f"{exc}\nDid you mean @{ref}?") from None
                raise
            return ConfigOperand(
                source="file",
                reference=located,
                overrides=overrides,
                extra_dirs=extra,
                label=ref,
            )

        with exit_on_refusal(prog=prog):
            # Typer collects every positional (overrides included) into operands;
            # anything Click left over (unknown options) joins them so the dash
            # rule refuses it.
            tokens = [*operands, *ctx.args]
            split_dash_rule(tokens)
            groups: list[tuple[str, list[str]]] = []
            for token in tokens:
                if "=" in token or token.startswith("~"):
                    if not groups:
                        raise RefusalError(
                            f"override {token!r} precedes every operand; overrides "
                            "apply to the operand they follow"
                        )
                    groups[-1][1].append(token)
                else:
                    groups.append((token, []))
            if len(groups) < 2:
                raise RefusalError("configuration comparison needs at least two operands")
            for _, group_overrides in groups:
                validate_override_tokens(group_overrides)
            result = compare_operands(
                tuple(_to_operand(ref, tuple(ovr)) for ref, ovr in groups),
                compose_spec=spec,
                level=level,  # type: ignore[arg-type]
                root_type=root,
                hashes=reg,
                command=command,
            )
            typer.echo(render_comparison(result))

    return compare_cmd
