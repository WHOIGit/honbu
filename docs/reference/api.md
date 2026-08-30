# API

Generated from the source. The docstrings carry the reasoning — restating them here
by hand would be a second source of truth for one surface, which is the drift Honbu
exists to remove.

## Core

::: honbu.core.model

::: honbu.core.markers
    options:
      members: [check_field_path, field_marker, identity_bindings]

::: honbu.core.hashes
    options:
      members: [HashRegistry, FieldHash, SourceHash, ByteHash, FannedHash, HashEntry, HashResult]

::: honbu.core.paths
    options:
      members: [is_cwd_relative, is_absolute_uri, sanitize_relative_path, path_length_refusal]

::: honbu.core.templates
    options:
      members: [TemplateContext, TemplateRegistry, TemplateFunctionSpec, render_template, template_value_paths]

::: honbu.core.io

::: honbu.core.identifiers

## Flow

::: honbu.flow.commands
    options:
      members: [CommandSpec, TargetSpec, StepSpec, Positional, CommandRegistry, parse_invocation]

::: honbu.flow.compose
    options:
      members: [ComposeSpec, compose_config, compose_file, build_search_path, split_argv, check_overrides, sweep_is_active, refuse_active_sweep_block]

::: honbu.flow.resolve
    options:
      members: [Resolution, resolve, anchor, strip_resolved_only]

::: honbu.flow.run
    options:
      members: [Run, open_run]

::: honbu.flow.manifest
    options:
      members: [Manifest]

::: honbu.flow.harness
    options:
      members: [Application, dispatch_command, Handler, load_composed]

## Operations

::: honbu.sweeps.definitions
    options:
      members: [SweepBlock, SweepDeclaration, Axis, AxisOption, Coordinate, check_root_declares_sweep, declared_block_type, sweep_block_of, without_sweep_block]

::: honbu.sweeps.plan
    options:
      members: [SweepPlan, plan_sweep]

::: honbu.sweeps.prepare
    options:
      members: [Preparation, PreparedChild, prepare_sweep, sweep_identity, read_sweep_index]

::: honbu.sweeps.progress
    options:
      members: [SweepStatus, ChildStatus, ExecutionResult, execute_sweep, child_argv, scan_sweep, refresh_sweep]

::: honbu.compare.configs
    options:
      members: [ConfigOperand, Comparison, Difference, compare_configs, compare_operands, render_comparison]

::: honbu.materialize.trees
    options:
      members: [MaterializationPlan, MaterializationRoot, MaterializationScope, MaterializedFile, plan_config_materialization, plan_materialization, apply_materialization]
