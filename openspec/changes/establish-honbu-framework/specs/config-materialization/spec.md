## ADDED Requirements

### Requirement: Packaged defaults are usable before a local tree exists
A project SHALL be able to ship a committed configuration tree inside its
installed package, and the framework SHALL compose from it when no local tree is
present, so a freshly installed project runs before its user has authored
anything. A local directory SHALL shadow the packaged one per group, so a checkout
overrides a shipped default without editing it.

#### Scenario: Fresh install composes
- **WHEN** a project with packaged defaults is invoked in a directory holding no
  configuration
- **THEN** composition succeeds from the packaged tree

#### Scenario: Local shadows packaged
- **WHEN** a local group shares a name with a packaged one
- **THEN** the local group is selected and unmentioned groups fall through

### Requirement: Materialization copies the closure of declared roots
Materialization SHALL copy packaged configuration into a local directory, starting
from declared **roots** — a configuration name, a configuration-group selection, or
an authored file — and including every packaged configuration those roots
transitively reference.

The closure SHALL be obtained from the compositor's own record of which group
options it selected, not by re-parsing defaults lists. A second implementation of
the compositor's selection rules would drift from the one that actually composes,
and the drift would be silent: the copied tree would compose differently from the
packaged one it was copied from.

The compositor's own internal groups SHALL be excluded from the closure.

#### Scenario: Root and its dependencies
- **WHEN** a root is materialized
- **THEN** the group options it selects are copied along with it

#### Scenario: Transitively referenced options
- **WHEN** a selected group option itself selects further options
- **THEN** those are copied too

#### Scenario: Framework-internal groups are not copied
- **WHEN** a closure is computed
- **THEN** the compositor's own configuration groups are excluded

#### Scenario: An authored file as a root
- **WHEN** an authored file outside the packaged tree is given as a root
- **THEN** the packaged options it references are copied and the file itself is
  left where it is

### Requirement: Scopes are named by the project
A project SHALL declare named **scopes**, each a set of materialization roots and
optionally a set of packaged non-configuration resources. The framework SHALL ship
no scope names, because which starters a project offers is entirely its own.

Scopes SHALL be additive, and a project SHALL be able to declare which apply when
a caller names none.

Non-configuration resources SHALL be materializable alongside configuration,
because a starter configuration that points at a fixture dataset is only useful if
the dataset arrives with it.

#### Scenario: Scope selection
- **WHEN** a caller names a scope
- **THEN** that scope's roots and resources are materialized

#### Scenario: Additive scopes
- **WHEN** two scopes are named
- **THEN** the union of their closures is materialized once, with no file copied
  twice

#### Scenario: Default scopes
- **WHEN** no scope is named
- **THEN** the project's declared defaults apply

#### Scenario: Fixture resource
- **WHEN** a scope declares a packaged resource directory
- **THEN** it is materialized so the starter configuration resolves against
  something that exists

### Requirement: Materialization is planned, additive, and reversible in intent
Materialization SHALL produce a **plan** classifying every file as created,
skipped because it already exists, or overwritten, and SHALL be able to report the
plan without writing.

Writing SHALL be additive by default: an existing file SHALL be skipped, never
silently replaced. Overwriting SHALL require an explicit instruction, and SHALL
still be reported per file.

The framework SHALL NOT delete a local file that the packaged tree no longer
contains. Materialization adds; deciding that a local file is obsolete requires
knowing whether a human edited it, which the framework cannot know.

#### Scenario: Dry run
- **WHEN** a plan is requested without writing
- **THEN** every action is reported and nothing is created

#### Scenario: Existing file is kept
- **WHEN** materialization would write a file that already exists
- **THEN** it is skipped and reported as skipped

#### Scenario: Explicit overwrite
- **WHEN** overwriting is requested
- **THEN** existing files are replaced and each replacement is reported

#### Scenario: Removed packaged file
- **WHEN** the packaged tree no longer holds a file the local tree does
- **THEN** the local file is left alone

### Requirement: Materialization is a function, and a command is optional
The framework SHALL expose materialization as functions a project calls from its
own command, and MAY additionally offer registration of a ready-made command. It
SHALL NOT reserve a command name, because the framework reserves no option that
governs how much of a command's work runs and reserving a command name would be
the same imposition by another route.

#### Scenario: Project declares its own command
- **WHEN** a project wants its own name, flags and help for materialization
- **THEN** it calls the functions from a command it declares

#### Scenario: Ready-made registration
- **WHEN** a project wants no opinion
- **THEN** it registers the provided command and gets the scopes it declared

### Requirement: A local tree can be compared against the packaged defaults
The framework SHALL support comparing a materialized local configuration against
the packaged configuration it was copied from, reporting what has diverged.

This SHALL reuse configuration comparison rather than introducing a separate
mechanism, and it SHALL be the answer to whether a shipped default has moved since
a tree was materialized — a question that otherwise has no answer, and which
already has consequences: continuing a run reapplies schema defaults at
continuation time, so a default that moved is silently picked up.

#### Scenario: Diverged local file
- **WHEN** a local configuration has been edited since it was materialized
- **THEN** comparison reports how it differs from the packaged original

#### Scenario: Shipped default moved
- **WHEN** a packaged configuration changed in a newer release
- **THEN** comparison reports the difference against the local copy
