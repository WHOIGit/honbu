# config-and-cli Specification

## Purpose

Defines Hydra configuration composition with search-path precedence, typed configuration-source loading (`default`, `file`, `resolved`, `fork`), bare-comma override refusal, passive `ProjectSpec` bundling, curated top-level exports, and opt-in CLI helpers (`honbu.cli`) and Typer presets (`honbu.cli.presets`) while projects own their CLI surface.

## Requirements

### Requirement: Composition and CLI presets are core dependencies, not extras
The compositor and Typer SHALL be core dependencies, while optional
execution-backend packages (such as `submitit` and `optuna`) SHALL remain outside
core dependencies. Layer boundaries SHALL be enforced by test: `honbu.core` SHALL
import no Hydra, OmegaConf, YAML, or Typer; `honbu.flow` and `honbu.cli`
top-level SHALL import no Typer.

#### Scenario: Fresh install composes and imports presets
- **WHEN** the framework is installed with no extras selected
- **THEN** composition, resolution, the run lifecycle, `honbu.cli`, and
  `honbu.cli.presets` all work

#### Scenario: Layering stays enforced by test
- **WHEN** imports are checked across `honbu.core`, `honbu.flow`, and `honbu.cli`
- **THEN** `honbu.core` imports no Hydra, OmegaConf, YAML, or Typer, and neither
  `honbu.flow` nor `honbu.cli` top-level imports Typer

### Requirement: Hydra composition with defined precedence
The framework SHALL compose configuration through the Hydra Compose API with a
defined search order — repeatable explicit directories, then a project-local
directory, then packaged defaults — and SHALL export that search-path assembly so
a project may drive composition itself. The highest-precedence directory
*containing the primary config* SHALL be the primary directory; the remainder
SHALL be group fallbacks in search order. Absence of the primary config from every
directory SHALL be refused, listing them.

#### Scenario: Local shadow
- **WHEN** a local group and a packaged group share a name
- **THEN** the local group is selected and omitted groups fall through to packaged

#### Scenario: Overlay supplies a missing group
- **WHEN** an explicit directory holds a group the primary directory lacks
- **THEN** that group resolves from the explicit directory

#### Scenario: Primary directory outranks a fallback for the same group
- **WHEN** an explicit directory holds a group of the same name as one in the
  primary directory, and does not itself carry the primary config
- **THEN** the primary directory's group is selected

#### Scenario: Overlay carrying the primary config takes over
- **WHEN** an explicit directory carries the primary config
- **THEN** it becomes the primary directory and its groups take precedence

#### Scenario: No primary config anywhere
- **WHEN** no directory on the search path holds the primary config
- **THEN** composition is refused, listing the directories searched

#### Scenario: Project drives composition
- **WHEN** a project needs a composition feature the framework does not wrap
- **THEN** it obtains the assembled search path and calls the compositor directly

### Requirement: Group selection is an override, not a root
Selecting a configuration group SHALL be an ordinary override rather than a
distinct root source, and the framework SHALL NOT reserve any group name for
promotion to the configuration root.

#### Scenario: Group override against the default root
- **WHEN** a command composes with a group selection and no explicit root
- **THEN** the default root composes and the selection is applied as an override

#### Scenario: No reserved group
- **WHEN** a project promotes a group to the root by its own convention
- **THEN** the framework treats its selection identically to any other override

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

### Requirement: Replaying a resolved configuration
Replaying a `resolved` configuration SHALL use its literal output directories
rather than re-rendering their templates, and the both-supplied refusal on a
directory and its template SHALL NOT apply, because a replayed configuration is
not authored.

#### Scenario: Replay lands in the original directory
- **WHEN** a run's resolved configuration is replayed without overrides
- **THEN** it resolves to that run's directory rather than a new one

#### Scenario: Replay with overrides
- **WHEN** a resolved configuration is replayed with overrides
- **THEN** a new configuration index and invocation are recorded against the run

### Requirement: Forking from a prior run
Loading a `fork` source SHALL read the parent run's authored (`composed.yaml`)
configuration, strip any resolved-only fields, apply overrides, and resolve a new
run directory. Forking SHALL require a verified parent artifact recorded in the
parent's manifest, selectable by artifact key at invocation time with an optional
default declared on the command. The parent's other artifacts SHALL NOT be copied.

#### Scenario: Fork re-resolves input paths
- **WHEN** a run is forked from a parent
- **THEN** the parent's composed configuration is the base, so input paths
  re-resolve against the forking machine rather than staying inherited absolute

#### Scenario: Fork discards resolved-only fields
- **WHEN** a forked configuration carries a resolved-only field
- **THEN** it is stripped before resolution so the child resolves its own
  directory and identities

#### Scenario: Caller selects which parent artifact to fork from
- **WHEN** a parent run registers multiple artifacts (such as `best_checkpoint`
  and `last_checkpoint`) and the caller selects one
- **THEN** lineage records both the chosen key and the artifact path, and an
  unknown key is refused listing the parent's registered artifacts

#### Scenario: Fork without a declared or supplied artifact
- **WHEN** a fork is attempted with neither a caller-supplied artifact key nor a
  default `fork_artifact` on the command
- **THEN** the fork is refused naming the missing artifact selection

#### Scenario: Parent artifacts are not copied
- **WHEN** a run is forked
- **THEN** only the lineage record is written; the parent's metrics, results, and
  manifest remain in place

### Requirement: A bare top-level comma in an override is refused
An override SHALL be refused when the compositor's grammar reads its value as a
multi-value sweep — a comma sitting directly in the value rather than inside a
collection literal or a quoted string — naming the sweep block as the place axes
are declared. The framework SHALL NOT run compositor multirun. Commas inside
collection literals, mapping literals, or quoted strings SHALL compose normally.

#### Scenario: Bare comma on an ordinary path
- **WHEN** an override supplies a bare comma-separated value for a configuration
  field
- **THEN** it is refused, naming the field and directing the caller to the sweep
  block

#### Scenario: List value composes
- **WHEN** an override supplies a bracketed list for a list-valued field
- **THEN** it composes as a single value

#### Scenario: Comma inside a quoted string
- **WHEN** an override supplies a quoted value containing a comma
- **THEN** it composes as one string

### Requirement: Projects own their CLI surface
Projects SHALL fully own their CLI library, command tree, flag names, option
types, help text, shell completion, and pre-open hooks. The framework SHALL NOT
ship a mandatory CLI dispatcher, SHALL NOT embed CLI options or positionals on
`CommandSpec`, and SHALL NOT hardcode CLI flag spellings inside `honbu.core` or
`honbu.flow`. Refusal messages in `honbu.core` and `honbu.flow` SHALL use
flag-agnostic wording by default and accept optional CLI hints.

#### Scenario: Project keeps full Typer control
- **WHEN** a project defines its commands in Typer with typed options, custom
  positionals, and domain checks between `resolve` and `open_run`
- **THEN** it calls `load_config`, `resolve`, and `open_run` directly with no
  framework dispatcher or raw argv handoff

#### Scenario: Custom flag names in collision refusals
- **WHEN** a project uses its own flag spellings and supplies a CLI hint
- **THEN** target collision refusals report the project's flag spellings rather
  than hardcoded framework flags

#### Scenario: No flag governs how much work runs
- **WHEN** a project wants to prepare a run without working it
- **THEN** it declares a non-advancing command rather than relying on a reserved
  framework flag

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
