## MODIFIED Requirements

### Requirement: Declared configuration sources and typed loader
The framework SHALL define four mutually exclusive configuration sources —
`default`, `file`, `resolved`, and `fork` — and expose a typed `load_config`
in `honbu.flow` with no CLI coupling. When loading `file` with a `compose_spec`
or `project`, composition SHALL search `[target.parent, *extra_dirs, local, packaged]`
so authored roots resolve unmaterialized packaged groups; `resolved` and `fork`
SHALL remain self-contained snapshots.

#### Scenario: Conflicting sources
- **WHEN** more than one configuration source is supplied
- **THEN** source selection fails naming the conflict

#### Scenario: Source the command does not accept
- **WHEN** an augmenting command is invoked with `default`
- **THEN** it is refused, naming the sources it does accept

#### Scenario: Command needing no configuration
- **WHEN** a command declaring no configuration source runs
- **THEN** no composition is attempted and a missing default root is not an error

#### Scenario: Overrides on an authored file
- **WHEN** an authored file is loaded with overrides
- **THEN** the overrides apply to it as they would to the default root

#### Scenario: Authored file resolves unmaterialized packaged groups
- **WHEN** `load_config("file", path, project=PROJECT)` composes a standalone
  root referencing a packaged group not copied locally
- **THEN** composition searches `[target.parent, *extra_dirs, local, packaged]`
  and resolves the packaged group cleanly

#### Scenario: Run directory names a prior configuration
- **WHEN** a prior run is named by its directory for `resolved` or `fork`
- **THEN** its persisted `resolved.yaml` or `composed.yaml` is located within it

### Requirement: Opt-in library-agnostic CLI helpers (`honbu.cli`)
`honbu.cli` SHALL expose library-agnostic helpers without importing Typer:
`validate_override_tokens`, `split_dash_rule`, `select_config_source`,
`resolve_positional_config` (two-channel deduplicated lookup across direct path
and search path, refusing 0 matches, ambiguous >1 unique matches, and run
directories), `exit_on_refusal`, `emit_warnings`, and `CONVENTIONAL_FLAGS`.

#### Scenario: Dash-rule catches mistyped options in overrides
- **WHEN** a dash-prefixed token lands in override tokens passed to
  `validate_override_tokens`
- **THEN** it is refused as an unknown option rather than forwarded to Hydra

#### Scenario: Compositor override prefixes pass validation
- **WHEN** override tokens use `+`, `++`, or `~` prefixes
- **THEN** they route to composition unchanged

#### Scenario: Refusal exit preserves defect tracebacks
- **WHEN** a command raises a `HonbuError` refusal under the refusal exit helper
- **THEN** the message is printed and the process exits non-zero without a
  traceback, while any unexpected exception preserves its traceback

#### Scenario: Structured error hook
- **WHEN** a command supplies an error formatter (for example under `--format json`)
- **THEN** refusals can be rendered in that format instead of plain stderr text

#### Scenario: Source selection is independent of flag spelling
- **WHEN** a project parses `--resolved-config` or a positional `<run_dir>` and
  passes the value to `select_config_source`
- **THEN** mutual exclusivity and command-accepted sources are enforced identically

#### Scenario: Forgiving positional config resolves shadowed match and refuses ambiguity
- **WHEN** `resolve_positional_config` runs on a token matching a local shadow
  of a packaged config versus a token matching two distinct filesystem paths
- **THEN** the shadowed match returns one path while the two distinct paths
  refuse as ambiguous listing both candidates and prompting `-c` / `--config`

#### Scenario: Run directory passed to positional config resolver
- **WHEN** a run directory containing `manifest.json` or `config/resolved.yaml`
  is passed to `resolve_positional_config`
- **THEN** it refuses hinting `--resolved` or `--fork-from`

### Requirement: Opt-in Typer presets (`honbu.cli.presets`)
`honbu.cli.presets` SHALL provide optional Typer presets: `HYDRA_CONTEXT_SETTINGS`,
option factories and `Annotated` aliases (`ConfigOpt` exposing `-c`/`--config`),
`build_materialize_command` (using `--clobber` and optional `--from <file>`),
and N-way `build_compare_command` accepting `@default`, `@packaged`,
`@packaged:<name>`, run directories, and forgiving config names/paths.

#### Scenario: One-line adoption of conventional Typer options
- **WHEN** a project imports option aliases from `honbu.cli.presets`
- **THEN** its commands share consistent flag names (including `-c`/`--config`),
  types, and help text while retaining full Typer validation and completion

#### Scenario: Customized flag spelling via factory
- **WHEN** a project uses an option factory to rename a flag or customize help
- **THEN** it keeps the preset's type and behavior under its own CLI spelling

#### Scenario: Project skips Typer presets
- **WHEN** a project uses Click, `argparse`, or hand-written Typer options
- **THEN** it never imports `honbu.cli.presets` and all of `honbu.core`,
  `honbu.flow`, and `honbu.cli` work identically

#### Scenario: Mountable materialize and compare builders use harmonized flags
- **WHEN** projects mount `build_materialize_command` or `build_compare_command`
- **THEN** materialize uses `--clobber` and `--from` while compare accepts
  N-way operands including `@default`, `@packaged`, and `@packaged:<name>`

### Requirement: Passive ProjectSpec bundle and curated top-level exports
`honbu.flow` SHALL export a passive frozen `ProjectSpec` dataclass bundling
`root_type`, `compose_spec`, `hashes`, `templates`, `config_hash_name`, and
optional `write_cli_txt: bool = False` (validating schema and identity fields
at construction with zero CLI or IoC coupling). Top-level `honbu` SHALL export
only the everyday schema, marker, hash, and error surface.

#### Scenario: Project constructs ProjectSpec once at import
- **WHEN** a project defines `PROJECT = ProjectSpec(root_type=RootConfig, compose_spec=SPEC, hashes=HASHES)`
- **THEN** `validate_root_contract` and `validate_identity_fields` run at import
  and callers can pass `project=PROJECT` across `load_config`, `resolve`,
  `open_run`, `compare_configs`, `prepare_sweep`, `plan_materialization`, and
  `cli.presets` without repeating shared arguments

#### Scenario: Direct kwargs still work without ProjectSpec
- **WHEN** a caller or test invokes `resolve`, `open_run`, or peer operations
  passing `root_type` and `hashes` directly without a `ProjectSpec`
- **THEN** every function continues to work identically

#### Scenario: Curated top-level namespace
- **WHEN** a user imports from top-level `honbu`
- **THEN** everyday schema, marker, hash, and error types are available directly
  while internal walker, target-clearing, and IO plumbing remain in `honbu.core.*`
