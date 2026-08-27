"""L2 — the flow: composition, declared commands, and the run record.

Opt-in. A project that wants only the mechanics never imports this package, which
is also why L2's dependencies live in the ``flow`` extra rather than the core: L1
cannot accidentally reach a composition primitive. Hydra is imported lazily inside
:func:`compose_config`, so this package stays importable without it.

The boundary that matters: composition ends at a plain dict. Everything after —
validation, identities, templates, paths — belongs to the resolver, which never
sees argv and never reads a configuration file.

What exists so far: composition and the argv grammar, command declarations, and
the live run manifest. The resolver that walks the fixed phase order, and the
lifecycle that drives it, are still to come.
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
    prior_config_path,
    resolved_config_path,
    selected_source,
    split_argv,
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
    "compose_config",
    "compose_file",
    "composed_config_path",
    "config_dir_for",
    "dispatch_command",
    "field_default",
    "initialize_run",
    "load_composed",
    "open_run",
    "parse_invocation",
    "prior_config_path",
    "resolve",
    "resolved_config_path",
    "resolved_digest",
    "selected_source",
    "split_argv",
    "write_bundle",
]
