"""L2 — the flow: composition, declared commands, and the run record.

A project that wants only the core never imports this package. That used to be
enforced by packaging — L2's dependencies lived in a ``flow`` extra, so ``core``
*could not* reach a composition primitive even by accident. The extra is gone (D20),
so the boundary is now asserted by a test instead: no module under ``core``
imports Hydra, OmegaConf or YAML.

The boundary that matters: composition ends at a plain dict. Everything after —
validation, identities, templates, paths — belongs to the resolver, which never
sees argv and never reads a configuration file.

The package owns composition and argv parsing, command declarations, resolution,
the lifecycle, and the live run manifest.
"""

from honbu.flow.commands import (
    CommandMode,
    CommandRegistry,
    CommandSpec,
    ParsedInvocation,
    Positional,
    StepSpec,
    TargetSpec,
    field_default,
    parse_invocation,
)
from honbu.flow.compose import (
    SWEEP_BLOCK,
    check_overrides,
    ALL_CONFIG_SOURCES,
    FRAMEWORK_OPTIONS,
    SOURCE_OPTIONS,
    ArgvSplit,
    ComposeSpec,
    ConfigSource,
    OptionSpec,
    build_search_path,
    compose_config,
    compose_file,
    composed_config_path,
    force_add_sweep_overrides,
    prior_config_path,
    refuse_active_sweep_block,
    resolved_config_path,
    selected_source,
    split_argv,
    sweep_is_active,
)
from honbu.flow.harness import (
    Application,
    Handler,
    BareFn,
    InspectFn,
    WorkFn,
    dispatch_command,
    load_composed,
)
from honbu.flow.manifest import RESERVED_EVENTS, Manifest, RunState
from honbu.flow.resolve import Resolution, anchor, resolve
from honbu.flow.run import (
    COMPOSED_NAME,
    CONFIG_DIRNAME,
    RESOLVED_NAME,
    Run,
    config_dir_for,
    initialize_run,
    open_run,
    resolved_digest,
    write_bundle,
)

__all__ = [
    "ALL_CONFIG_SOURCES",
    "FRAMEWORK_OPTIONS",
    "RESERVED_EVENTS",
    "SOURCE_OPTIONS",
    "Application",
    "ArgvSplit",
    "COMPOSED_NAME",
    "CONFIG_DIRNAME",
    "RESOLVED_NAME",
    "CommandMode",
    "CommandRegistry",
    "CommandSpec",
    "ComposeSpec",
    "ConfigSource",
    "BareFn",
    "Handler",
    "InspectFn",
    "Manifest",
    "OptionSpec",
    "ParsedInvocation",
    "Positional",
    "Resolution",
    "Run",
    "RunState",
    "StepSpec",
    "TargetSpec",
    "WorkFn",
    "anchor",
    "build_search_path",
    "SWEEP_BLOCK",
    "check_overrides",
    "compose_config",
    "compose_file",
    "composed_config_path",
    "config_dir_for",
    "dispatch_command",
    "field_default",
    "force_add_sweep_overrides",
    "initialize_run",
    "load_composed",
    "open_run",
    "parse_invocation",
    "prior_config_path",
    "refuse_active_sweep_block",
    "resolve",
    "resolved_config_path",
    "resolved_digest",
    "selected_source",
    "split_argv",
    "sweep_is_active",
    "write_bundle",
]
