## ADDED Requirements

### Requirement: Composition is a core capability, not an extra
The compositor SHALL be a core dependency, so every install can compose, persist
and run without selecting an extra, and no code path SHALL exist whose only purpose
is to report a missing optional dependency at invocation time.

Layers SHALL remain boundaries of *import* rather than of installation: a project
opts out of a capability by not importing it. Because installation no longer
enforces the boundary, the framework SHALL assert it instead — no module in the
core layer SHALL import the compositor, its configuration library, or a YAML
implementation, and this SHALL be verified by test rather than by convention.

Genuine execution backends — job submitters, hyperparameter search engines — SHALL
remain optional dependencies, because they are substitutable infrastructure the
framework adapts to rather than capabilities it implements.

#### Scenario: Fresh install composes
- **WHEN** the framework is installed with no extras selected
- **THEN** composition, resolution, persistence and the run lifecycle all work

#### Scenario: Core stays independent of the compositor
- **WHEN** a core-layer module is checked for its imports
- **THEN** it imports no compositor, configuration library, or YAML implementation

#### Scenario: A launcher is absent
- **WHEN** a project requests an execution backend whose package is not installed
- **THEN** it is refused naming the extra to install, because that backend is
  genuinely optional

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
- **THEN** the primary directory's group is selected, because the compositor ranks
  its primary directory above every search-path fallback

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
- **WHEN** a command is invoked with a group selection and no explicit root
- **THEN** the default root composes and the selection is applied as an override

#### Scenario: No reserved group
- **WHEN** a project promotes a group to the root by its own convention
- **THEN** the framework treats its selection identically to any other override

### Requirement: Declared configuration sources
The framework SHALL define four mutually exclusive configuration sources — the
default config name, an authored file, a prior run's resolved configuration given
as a file or a run directory, and a parent run to continue from — and SHALL allow
overrides on top of any of them. A command SHALL declare which sources it accepts;
a command declaring none SHALL compose nothing.

#### Scenario: Conflicting sources
- **WHEN** more than one configuration source is supplied
- **THEN** composition fails naming the conflict

#### Scenario: Source the command does not accept
- **WHEN** an augmenting command is invoked with a freshly composed root
- **THEN** it is refused, naming the sources it does accept

#### Scenario: Command needing no configuration
- **WHEN** a command declaring no configuration source runs
- **THEN** no composition is attempted and a missing default root is not an error

### Requirement: Replaying a resolved configuration
Replaying a resolved configuration SHALL use its literal output directories rather
than re-rendering their templates, and the both-supplied refusal on a directory
and its template SHALL NOT apply, because a replayed configuration is not authored.

#### Scenario: Replay lands in the original directory
- **WHEN** a run's resolved configuration is replayed without overrides
- **THEN** it resolves to that run's directory rather than a new one

#### Scenario: Replay with overrides
- **WHEN** a resolved configuration is replayed with overrides
- **THEN** a new configuration index and invocation are recorded against the run

### Requirement: Dash-rule token grammar
Tokens containing an assignment and no leading dash SHALL be composition
overrides. Dash-prefixed tokens SHALL be options, and an undeclared dash-prefixed
token SHALL fail as a command error. Remaining bare tokens SHALL be positionals,
matched against the command's declaration. Positionals SHALL reach the command and
SHALL NOT assign configuration fields.

#### Scenario: Mixed tokens
- **WHEN** a caller supplies an override, an option, and a positional
- **THEN** each is routed to composition, the command, and the command's
  positionals respectively

#### Scenario: Compositor override prefixes
- **WHEN** an override uses an addition, forced-addition or deletion prefix
- **THEN** it routes to composition unchanged

#### Scenario: Option value is not mistaken for a positional
- **WHEN** a value-taking option is given its value as a separate token
- **THEN** that value is consumed by the option and not treated as a positional

#### Scenario: Unknown option
- **WHEN** a dash-prefixed token is neither a framework option nor declared by the
  command
- **THEN** the invocation fails as a command error

#### Scenario: Positional arity
- **WHEN** a required positional is missing or an extra bare token is supplied
- **THEN** the invocation fails naming the command's declared positionals

### Requirement: A bare top-level comma in an override is refused
An override SHALL be refused when the compositor's grammar reads its value as a
multi-value sweep — a comma sitting directly in the value rather than inside a
collection literal or a quoted string — naming the sweep block as the place axes
are declared. The framework SHALL NOT run compositor multirun.

The two spellings differ by one character and mean opposite things: a bare comma
requests one run per listed value, while a bracketed list is a single list-valued
setting. Silently accepting the first would either discard every value but one or
smuggle in an execution model the framework does not own.

Commas that are structurally part of a value SHALL compose normally — collection
literals, mapping literals, and quoted strings all contain commas that are not
top-level.

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

### Requirement: A framework option applies only where the command's declaration implies it
Each framework option's applicability SHALL be derived from what the command
already declares, and an inapplicable option SHALL be refused at parse time naming
why. Help SHALL be generated from the same derivation, so the option surface a
command advertises and the one it accepts cannot differ.

Applicability SHALL NOT be a separate per-command registration. Every framework
option is already implied by an existing declaration — search directories apply
where the command composes, permission to clear applies where the command declares
something clearable, and resume applies where the command produces into a target —
so a second declaration could only drift from the first.

The framework SHALL NOT reserve an option that decides how much of a command's
work is performed. Which configuration is used, and whether the framework may
write where it intends to, are settled before any project code runs and are the
framework's to own; how far the work proceeds is the project's, and is expressed
by declaring a command rather than by a framework flag.

#### Scenario: Clearing offered only where something is clearable
- **WHEN** a command that writes nothing is given the clobber option
- **THEN** it is refused, naming the command and why the option does not apply

#### Scenario: Help matches the parser
- **WHEN** a command's help omits an option
- **THEN** supplying that option is refused rather than silently ignored

#### Scenario: Project options are unaffected
- **WHEN** a command declares its own options
- **THEN** they are accepted alongside the applicable framework ones

#### Scenario: No option governs how much work runs
- **WHEN** a project wants to prepare a run without working it
- **THEN** it declares a command that does so, rather than passing a framework flag

### Requirement: Installed command surface
The framework SHALL provide a command harness a project registers its
subcommands with, threading each through composition, resolution and the run
lifecycle, and exiting cleanly on a user-facing refusal. The harness SHALL derive
its command tree, option surface and help text from the command declarations
rather than from a second, parallel declaration in a CLI library.

#### Scenario: Refusal exit
- **WHEN** a command raises a user-facing refusal
- **THEN** the process prints the message and exits non-zero without a traceback

#### Scenario: Unexpected error is not swallowed
- **WHEN** a command raises anything that is not a user-facing refusal
- **THEN** the traceback is preserved, because it is a defect rather than a
  message to the user

#### Scenario: Help from the declaration
- **WHEN** help is requested for a command
- **THEN** its positionals, options and accepted configuration sources are listed
  from its declaration

#### Scenario: Longest command path wins
- **WHEN** a registered command's name has more segments than another's prefix
- **THEN** the longer path is dispatched

#### Scenario: Project keeps its own CLI library
- **WHEN** a project prefers to own argument parsing
- **THEN** it can invoke one command's full flow directly with the remaining
  tokens, without the framework's dispatcher

### Requirement: Configuration sources are loaded by the harness
The harness SHALL load whichever configuration source the invocation selected —
the default root, an authored file, a prior run's resolved configuration, or a
parent run to continue from — and SHALL apply overrides on top of any of them. A
prior run MAY be named by its directory rather than its file. Continuing SHALL
record the parent's identity and the artifact continued from, which the command
declares.

#### Scenario: Overrides on an authored file
- **WHEN** an authored root file is supplied with overrides
- **THEN** the overrides apply to it as they would to the default root

#### Scenario: Run directory names a prior configuration
- **WHEN** a prior run is named by its directory
- **THEN** its persisted resolved configuration is located within it

#### Scenario: Continuing builds on the authored configuration
- **WHEN** a run is continued from a parent
- **THEN** the parent's composed configuration is the base, so its input paths
  re-resolve against the continuing machine rather than being inherited absolute

#### Scenario: Continuing discards the parent's resolved-only values
- **WHEN** a continued configuration carries a resolved-only field
- **THEN** it is stripped before resolution, so the child resolves its own
  directory and identities rather than inheriting them

#### Scenario: The parent's other artifacts are not copied
- **WHEN** a run is continued
- **THEN** only the lineage record is written, and the parent's metrics, results
  and manifest stay where they are

#### Scenario: Continuing declares its artifact
- **WHEN** a command accepts the continue source but names no artifact to continue
  from
- **THEN** registration fails
