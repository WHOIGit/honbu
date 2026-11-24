"""Pure-Python flow: composition, typed source loading, commands, resolution, and runs.

A project that wants only the core never imports this package. Layer boundaries
are enforced by ``test_layering``: ``honbu.core`` imports no Hydra/OmegaConf/YAML/Typer,
and ``honbu.flow`` imports no Typer and owns zero CLI parsing or hardcoded flag
names (D19, D20).

The boundary that matters: composition ends at a plain dict. Everything after —
validation, identities, templates, paths — belongs to the resolver, which never
reads a configuration file.
"""

from honbu.flow.commands import (
    CommandMode,
    CommandRegistry,
    CommandSpec,
    ResumePolicy,
    StepSpec,
    TargetSpec,
    field_default,
)
from honbu.flow.compose import (
    ALL_CONFIG_SOURCES,
    SWEEP_AXIS_CONTAINERS,
    SWEEP_BLOCK,
    ComposeSpec,
    Composition,
    ConfigSource,
    build_search_path,
    check_overrides,
    compose_config,
    compose_file,
    compose_with_choices,
    composed_config_path,
    force_add_sweep_overrides,
    prior_config_path,
    refuse_active_sweep_block,
    resolved_config_path,
    sweep_is_active,
)
from honbu.flow.load import LoadedConfig, load_config, parent_record
from honbu.flow.manifest import RESERVED_EVENTS, Manifest, RunState
from honbu.flow.project import ProjectSpec
from honbu.flow.resolve import Resolution, anchor, resolve, strip_resolved_only
from honbu.flow.run import (
    COMPOSED_NAME,
    CONFIG_DIRNAME,
    RESOLVED_NAME,
    Run,
    bundle_bytes,
    config_dir_for,
    initialize_run,
    open_run,
    resolved_digest,
    write_bundle,
)

__all__ = [
    "ALL_CONFIG_SOURCES",
    "COMPOSED_NAME",
    "CONFIG_DIRNAME",
    "RESERVED_EVENTS",
    "RESOLVED_NAME",
    "SWEEP_AXIS_CONTAINERS",
    "SWEEP_BLOCK",
    "CommandMode",
    "CommandRegistry",
    "CommandSpec",
    "ComposeSpec",
    "Composition",
    "ConfigSource",
    "LoadedConfig",
    "Manifest",
    "ProjectSpec",
    "Resolution",
    "ResumePolicy",
    "Run",
    "RunState",
    "StepSpec",
    "TargetSpec",
    "anchor",
    "build_search_path",
    "bundle_bytes",
    "check_overrides",
    "compose_config",
    "compose_file",
    "compose_with_choices",
    "composed_config_path",
    "config_dir_for",
    "field_default",
    "force_add_sweep_overrides",
    "initialize_run",
    "load_config",
    "open_run",
    "parent_record",
    "prior_config_path",
    "refuse_active_sweep_block",
    "resolve",
    "resolved_config_path",
    "resolved_digest",
    "strip_resolved_only",
    "sweep_is_active",
    "write_bundle",
]
