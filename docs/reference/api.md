# API

Generated from the source. The docstrings carry the reasoning — restating them here
by hand would be a second source of truth for one surface, which is the drift Honbu
exists to remove.

## Core

::: honbu.core.model

::: honbu.core.blocks
    options:
      members: [ExperimentConfig, RuntimeConfig, HashedRuntimeConfig, OutputBlock, OutputLeaf, validate_root_contract]

::: honbu.core.markers
    options:
      members: [check_field_path, field_marker, identity_bindings, unmarked_path_fields]

::: honbu.core.hashes
    options:
      members: [HashRegistry, FieldHash, SourceHash, ByteHash, FannedHash, HashEntry, HashResult, project_fields, project_model]

::: honbu.core.paths
    options:
      members: [is_cwd_relative, is_absolute_uri, sanitize_relative_path, path_length_refusal]

::: honbu.core.templates
    options:
      members: [TemplateContext, TemplateRegistry, TemplateFunctionSpec, render_template, template_value_paths]

::: honbu.core.io

::: honbu.core.identifiers

## Flow

::: honbu.flow.project
    options:
      members: [ProjectSpec]

::: honbu.flow.commands
    options:
      members: [CommandSpec, TargetSpec, StepSpec, CommandRegistry]

::: honbu.flow.compose
    options:
      members: [ComposeSpec, compose_config, compose_file, build_search_path, check_overrides, sweep_is_active, refuse_active_sweep_block]

::: honbu.flow.load
    options:
      members: [LoadedConfig, load_config, parent_record]

::: honbu.flow.resolve
    options:
      members: [Resolution, resolve, anchor, strip_resolved_only]

::: honbu.flow.run
    options:
      members: [Run, open_run, initialize_run]

::: honbu.flow.manifest
    options:
      members: [Manifest]

## CLI

::: honbu.cli.helpers
    options:
      members: [CONVENTIONAL_FLAGS, DashRuleSplit, split_dash_rule, validate_override_tokens, select_config_source, exit_on_refusal, emit_warnings]

::: honbu.cli.presets
    options:
      members: [HYDRA_CONTEXT_SETTINGS, config_option, resolved_option, fork_from_option, fork_artifact_option, config_dirs_option, clobber_option, resume_option, build_materialize_command, build_compare_command]

## Operations

::: honbu.sweeps.definitions
    options:
      members: [SweepBlock, SweepDeclaration, SweepOutputBlock, Axis, AxisOption, Coordinate, check_root_declares_sweep, declared_block_type, sweep_block_of, without_sweep_block]

::: honbu.sweeps.plan
    options:
      members: [SweepPlan, plan_sweep, sweep_composers]

::: honbu.sweeps.prepare
    options:
      members: [Preparation, PreparedChild, SkippedChild, prepare_sweep, sweep_identity, read_sweep_index]

::: honbu.sweeps.progress
    options:
      members: [SweepStatus, ChildStatus, ExecutionResult, FailedChild, execute_sweep, child_argv, scan_sweep, refresh_sweep]

::: honbu.compare.configs
    options:
      members: [ConfigOperand, Comparison, Difference, HashComparison, compare_configs, compare_operands, render_comparison]

::: honbu.compare.runs
    options:
      members: [Objective, ReconciliationResult, GateCheck, CompatibilityReport, RankedRun, UnrankedRun, RunComparison, reconcile_hash, check_run_compatibility, compare_runs, compare_sweep, render_run_comparison]

::: honbu.materialize.trees
    options:
      members: [MaterializationPlan, MaterializationRoot, MaterializationScope, MaterializedFile, FileDrift, plan_materialization, apply_materialization, render_plan, compare_materialized_config, materialized_file_drift, render_file_drift]
