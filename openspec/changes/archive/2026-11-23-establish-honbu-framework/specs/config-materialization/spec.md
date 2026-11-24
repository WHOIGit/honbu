## Purpose

Defines configs-only materialization of packaged Hydra defaults into a working directory using compositor dependency closures, project-named scopes, additive non-deleting plans, and composed plus file-level local-versus-packaged drift reporting.

## ADDED Requirements

### Requirement: Packaged defaults are usable before a local tree exists
A project SHALL be able to ship a committed configuration tree inside its
installed package, and the framework SHALL compose from it when no local tree is
present, so a freshly installed project runs before its user authors anything. A
local directory SHALL shadow the packaged one per group.

#### Scenario: Fresh install composes
- **WHEN** a project with packaged defaults is invoked in a directory holding no
  local configuration
- **THEN** composition succeeds from the packaged tree

#### Scenario: Local shadows packaged
- **WHEN** a local group shares a name with a packaged one
- **THEN** the local group is selected and unmentioned groups fall through

### Requirement: Materialization is configs-only and copies the closure of declared roots
Materialization SHALL copy packaged configuration files only (never fixture
datasets or non-configuration data assets), starting from declared **roots** —
packaged configuration names with optional overrides, or an external authored
file — and copying every packaged configuration those roots transitively select.
Closures SHALL come from `hydra.runtime.choices`, excluding `hydra/*` internal
groups and stripping `@package` suffixes before mapping groups to packaged paths.

#### Scenario: Root and its transitive dependencies
- **WHEN** a packaged root is materialized
- **THEN** the root and every packaged group option it transitively selects are
  copied

#### Scenario: Package-relocated group options resolve cleanly
- **WHEN** a defaults list uses `@package` syntax (such as
  `/backbone/torchvision@model.image_input.backbone: efficientnet_b0`)
- **THEN** the `@package` suffix is stripped and `backbone/torchvision/efficientnet_b0.yaml`
  is materialized

#### Scenario: Framework-internal groups are excluded
- **WHEN** a closure is computed
- **THEN** `hydra/*` internal groups are excluded from the copy plan

#### Scenario: An authored file as a root
- **WHEN** an external authored YAML file outside the packaged tree is given as
  a root
- **THEN** only the packaged options it transitively references are copied into
  the local tree, and the authored file itself is left where it is

### Requirement: Project-named scopes over closure roots and config-tree files
A project SHALL declare named **scopes** via `MaterializationScope` holding
closure `roots`, optional verbatim config-tree `files` (such as non-YAML config
files or directories inside packaged defaults), and optional `exclude` globs.
Honbu SHALL ship no scope names. Scopes SHALL be additive with project-declared
defaults. Directory materialization SHALL exclude Python package plumbing
(`__init__.py`, `__pycache__/`, `*.pyc`, `*.pyo`) by default.

#### Scenario: Scope selection and additive union
- **WHEN** one or more scopes are selected
- **THEN** the union of their resolved config files is materialized once with no
  duplicate copies

#### Scenario: Default scopes
- **WHEN** no scope is named
- **THEN** the project's declared default scopes apply

#### Scenario: Verbatim config-tree files and plumbing exclusions
- **WHEN** a scope includes a directory or a non-YAML config file (such as a TOML
  rendering style)
- **THEN** those config files are materialized while `__init__.py`,
  `__pycache__/`, `*.pyc`, `*.pyo`, and scope-excluded globs (such as
  `AGENTS.md`) are skipped

### Requirement: Materialization is planned, additive, and never deletes
A unified `plan_materialization` SHALL derive the packaged root from
`ComposeSpec` (with an optional override for unpacked test trees) and return a
`MaterializationPlan` classifying each file as `create`, `skip`, or `overwrite`,
exposing `.created`, `.skipped`, `.overwritten`, and a plain-text `render_plan`
renderer. Applying a plan SHALL be additive by default, overwrite only when
explicitly requested, and SHALL NOT delete local files absent from the package.

#### Scenario: Dry run
- **WHEN** a plan is created and rendered without calling `apply_materialization`
- **THEN** every `create`, `skip`, and `overwrite` action is reported and nothing
  is written

#### Scenario: Existing file is kept by default
- **WHEN** materialization encounters an existing local file without overwrite
- **THEN** it is classified and reported as `skip` and left untouched

#### Scenario: Explicit overwrite
- **WHEN** overwriting is requested
- **THEN** existing target files are classified as `overwrite` and replaced on
  apply

#### Scenario: Local-only files are preserved
- **WHEN** a local config directory holds files not present in the packaged tree
- **THEN** materialization leaves them untouched

### Requirement: Materialization is a library first, with an optional CLI builder
The framework SHALL expose materialization as pure Python functions in
`honbu.materialize`, and MAY offer an optional Typer command builder in
`honbu.cli.presets`. It SHALL NOT reserve a command name.

#### Scenario: Project writes its own command
- **WHEN** a project wants custom flags, layout, or extra dataset steps
- **THEN** it calls `honbu.materialize` functions from its own CLI command

#### Scenario: Ready-made Typer builder
- **WHEN** a project wants a standard materialization command with no boilerplate
- **THEN** it mounts the optional builder from `honbu.cli.presets` under any
  command name it chooses

### Requirement: Composed local-versus-packaged drift comparison
`compare_materialized_config` SHALL compare the packaged-only configuration
(composed with `ComposeSpec.local = None`) against the effective local
configuration (local directory shadowing packaged defaults), accepting optional
`overrides` and reusing `honbu.compare.compare_configs` across all four
comparison levels (`literal`, `authored`, `behavioral`, `by_hash`). Partial local
trees SHALL compare cleanly without requiring a full local copy.

#### Scenario: Partial local shadow compares cleanly
- **WHEN** a local tree overrides only one group and relies on packaged
  fallthrough for the rest
- **THEN** `compare_materialized_config` succeeds and reports only the effective
  differences

#### Scenario: Non-default group checked via overrides
- **WHEN** `compare_materialized_config` is given `overrides` selecting a
  non-default group or experiment
- **THEN** both packaged and local-shadowed sides compose with those overrides
  and report behavioral or hash-attributed drift

### Requirement: File-level tree drift reporting
`materialized_file_drift` SHALL compare files across a local config directory
against packaged defaults (applying package-plumbing and optional exclusions) and
classify each path as `modified`, `unchanged`, `local_only`, or `packaged_only`,
without a lockfile, paired with a plain-text `render_file_drift` renderer.

#### Scenario: Post-upgrade file inventory
- **WHEN** a user runs `materialized_file_drift` after editing local configs or
  upgrading the package
- **THEN** every differing file is reported as `modified`, identical files as
  `unchanged`, custom local files as `local_only`, and unmaterialized packaged
  files as `packaged_only`
