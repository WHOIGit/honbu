## ADDED Requirements

### Requirement: Declared commands own their write scope
A project SHALL declare each command with a name that is a path through the
command tree, whether it produces a run directory or augments an existing one,
the write scope it owns, and the hashes it computes. Declared dotted paths SHALL
be validated against the schema when the command registry is constructed. A
command declaring no target SHALL be structurally incapable of clearing anything.

#### Scenario: Two commands, two targets
- **WHEN** two commands over one root configuration declare different targets
- **THEN** each clears only its own, and neither can reach the other's

#### Scenario: Read-only command
- **WHEN** a command declares no target
- **THEN** no destructive operation is available to it

#### Scenario: Mistyped declared path
- **WHEN** a command declares a target or scope path that is not a schema field
- **THEN** registry construction fails naming the path

#### Scenario: Command name with an internal separator
- **WHEN** a command's name has two segments and the second contains a hyphen
- **THEN** the flattened and the invocation forms are both recoverable

### Requirement: Producing and augmenting commands
A producing command SHALL create and own a run directory. An augmenting command
SHALL require an existing run directory holding a manifest, SHALL NOT re-render
the run-directory template, and SHALL own only the leaves it declared.

#### Scenario: Augmenting an existing run
- **WHEN** an augmenting command runs against a completed run directory
- **THEN** it inherits the resolved run directory, appends to the existing
  manifest, and writes only within its declared leaves

#### Scenario: Producing against an occupied directory
- **WHEN** a producing command resolves onto a directory holding a completed run
- **THEN** it refuses, describing the occupant

### Requirement: Fixed resolution phase order
Resolution SHALL proceed in a fixed, documented phase order: validate the composed
configuration; resolve input paths; compute config-only hashes for the active
command; assign identities derived from those hashes; render the command's target
template; resolve output leaves against the target; revalidate. No phase SHALL
depend on a later phase.

#### Scenario: Identity feeds a path template
- **WHEN** a target template references a configuration identity
- **THEN** the identity is computed before the template is rendered

#### Scenario: Leaf anchored to the rendered target
- **WHEN** an output leaf is relative
- **THEN** it resolves against the target directory rendered in the prior phase

### Requirement: Command-aware side-effect-free resolution
Resolution SHALL be command-aware and SHALL perform no filesystem writes; it
SHALL derive identities and resolve output paths for the active command, and MAY
resolve without external inputs for read-only inspection, reporting
input-reading identities as deferred. Resolution SHALL return its warnings rather
than emitting them. Resolution SHALL take the composed configuration, the command,
the invocation context, and — for an augmenting command — the existing run
manifest whose directory it inherits.

#### Scenario: Read-only inspection without inputs
- **WHEN** a configuration is resolved for inspection with its inputs absent
- **THEN** configuration identity and active output paths resolve and
  input-reading identities are reported as deferred

### Requirement: Resolution and run are separate objects
Resolution SHALL return an object exposing the two configuration views, the
computed hash entries, the deferred set, the resolved target and the warnings, and
carrying no method that writes. The lifecycle SHALL construct a separate run object
around it once the target is prepared; that object SHALL own the manifest and every
write, and SHALL be what work receives.

#### Scenario: Inspection cannot write
- **WHEN** a configuration is resolved for read-only inspection
- **THEN** the returned object offers no operation that touches the filesystem

#### Scenario: Work writes through the run
- **WHEN** work registers an artifact or completes a deferred hash
- **THEN** it does so through the run object, which is the single writer

### Requirement: Revalidation re-runs project validators
Because the final phase revalidates the tree, every project model validator SHALL
run a second time against a tree whose identities are assigned and whose paths are
absolute. A project validator SHALL therefore hold for a resolved tree as well as
a composed one.

#### Scenario: Validator that only holds before resolution
- **WHEN** a project validator asserts something true only of an unresolved tree
- **THEN** it passes composition and fails revalidation, rather than failing silently

### Requirement: A target template may not reference a deferred value
A command's target template SHALL NOT reference a value produced by a deferred
hash. This SHALL be checked twice: against the committed schema default when the
command registry is constructed, and against the template actually in force when
the target renders. The second check is not redundant — a composed configuration
may override the template, so the import-time check sees a string that never runs.

#### Scenario: Output directory named after a byte-reading digest
- **WHEN** a command's target template references an input-reading hash
- **THEN** registry construction fails, naming the command and the token

#### Scenario: Deferred value reached through an overridden template
- **WHEN** a composed configuration overrides the target template with one
  naming a field a deferred hash feeds
- **THEN** resolution refuses before the target is rendered, rather than naming
  the run directory after a value that is still null

### Requirement: Pure template rendering
Template rendering SHALL be pure: the same template and values SHALL always
render the same string. Clock- and entropy-derived values SHALL be produced once
per invocation by the resolver and supplied to the renderer, never generated
inside it.

#### Scenario: Repeated render
- **WHEN** the same template is rendered twice within one resolution
- **THEN** both renderings are identical

#### Scenario: Fresh identity per invocation
- **WHEN** the same composed configuration is resolved in two invocations
- **THEN** their fresh identities differ so their output targets do not collide

### Requirement: Output-path template grammar
Output paths SHALL support dotted resolved-configuration tokens, an
invocation namespace for framework-supplied tokens, registered token functions,
and format specifications; a token referencing an unknown value or an
unregistered function SHALL fail rather than fall back. A token naming a
configuration value SHALL fail when that value is null: a present-but-null field
is not the same as the invocation namespace, and treating it as such would let a
function that also has a no-value form silently substitute fresh entropy for a
value the author expected. Substituted values and
function results SHALL be sanitized as path components except where the source
field is path-marked or the function declares that it emits a whole path.

#### Scenario: Templated run directory
- **WHEN** a run template references experiment, timestamp, and run identity
- **THEN** it renders to a directory under the output root

#### Scenario: Unknown token
- **WHEN** a template references a value or function that does not exist
- **THEN** rendering fails naming the token

#### Scenario: Null-valued token
- **WHEN** a template names a configuration field that exists but is null
- **THEN** rendering fails naming the field, rather than rendering the token as
  though no value had been supplied

#### Scenario: Whole-path token
- **WHEN** a token whose value is an absolute path is substituted
- **THEN** its separators survive rather than being sanitized away

### Requirement: Project-extensible token functions
The framework SHALL provide a registry of template token functions a project
extends with its own, and the project SHALL decide whether overriding a
framework-provided function is permitted.

#### Scenario: Project function
- **WHEN** a project registers a token function and uses it in a template
- **THEN** the template renders through the project's function

### Requirement: Directory literal and directory template
An output block or leaf SHALL express its location either as a literal directory
or as a directory template, never both. Supplying both in the composed
configuration SHALL be refused at resolution rather than by the schema, because
the resolved configuration legitimately carries both. Neither field SHALL enter
any digest.

#### Scenario: Both supplied
- **WHEN** a composed configuration supplies both a directory and its template
- **THEN** resolution refuses, naming the block

#### Scenario: Template survives resolution
- **WHEN** a directory template is rendered into its directory field
- **THEN** the template remains in the resolved configuration as the authored record

### Requirement: Path resolution semantics
An output root SHALL resolve to an absolute path. An absolute or
working-directory-relative directory SHALL be stored absolute. A bare-relative
directory, including one rendered from a template, SHALL be stored unchanged and
anchored to the output root; an output leaf SHALL be stored unchanged and anchored
to its block's directory.

A field SHALL be recognized as an output block by its base type, however the field
is annotated — optional, unioned, or wrapped. Recognition decides which paths are
anchored to the output root rather than absolutized against the working directory,
so a narrower test would silently relocate an optional block's output.

#### Scenario: Portable resolved configuration
- **WHEN** a runs tree containing a resolved configuration is moved
- **THEN** its bare-relative output paths still resolve correctly

#### Scenario: Explicitly relative directory
- **WHEN** a directory is authored beginning with a working-directory-relative
  prefix
- **THEN** it is stored absolute, so no stored relative value is ambiguous

#### Scenario: Optionally-typed output block
- **WHEN** an output block field is annotated as optional rather than as a bare
  block type
- **THEN** its directory is still anchored to the output root, not absolutized
  against the working directory

### Requirement: Two views of one resolved configuration
Resolution SHALL return both a portable view, as persisted, and an absolute view
with every path joined, of the same type. The two SHALL produce identical digests.

#### Scenario: Absolute view is directly usable
- **WHEN** work reads an output leaf from the absolute view
- **THEN** the value is an absolute path requiring no further joining

#### Scenario: Same configuration
- **WHEN** both views are hashed
- **THEN** their digests are equal

### Requirement: Collision safety
A mutating command SHALL refuse existing content within its declared write scope
by default. Under an explicit override it SHALL clear only that scope, and only
where the scope lies strictly within the resolved containment root the command
declares. Refusal SHALL describe what occupies the target, including the state
and identity of any run recorded there.

#### Scenario: Existing output
- **WHEN** content exists in scope without the override
- **THEN** the command fails before modifying it, describing the occupant

#### Scenario: Scope confinement
- **WHEN** an augmenting command is given the override
- **THEN** only its declared leaves are cleared and the rest of the run is untouched

#### Scenario: Target outside the containment root
- **WHEN** a resolved target does not lie within the declared containment root
- **THEN** clearing is refused

#### Scenario: Case-colliding sibling
- **WHEN** a sibling directory differs from the target only in letter case
- **THEN** resolution warns that the two are one directory on Windows

### Requirement: Declared steps
A command's named steps, each with its own write scope and a resume policy, SHALL
be declared. Entering a step SHALL prepare that scope and record its boundaries on the
run timeline; the scope SHALL govern what the framework clears, not what the
project is able to write, and the framework SHALL NOT claim to sandbox project
writes. An undeclared step name and a nested step SHALL both be refused. An
exception within a step SHALL fail the command and prevent subsequent steps from
running, leaving the step's partial writes in place.

#### Scenario: Undeclared step
- **WHEN** work enters a step name the command did not declare
- **THEN** it is refused

#### Scenario: Failure stops the sequence
- **WHEN** a step raises
- **THEN** the command fails, remaining steps do not run, and completed steps
  remain recorded as completed

### Requirement: A step declares whether resuming continues or restarts it
A step SHALL declare whether its own prior output is resume state to be continued
from, or spoiled work to be discarded. Continuing SHALL be the default, because a
step whose output is its resume state loses irrecoverable work if wrongly
restarted, whereas a step wrongly continued produces a visible wrong result.

#### Scenario: Training resumes from its own checkpoint
- **WHEN** a training step is interrupted mid-run and the run is resumed
- **THEN** its checkpoints are intact and available to it

#### Scenario: A restarting step discards its partial output
- **WHEN** a step declared to restart is resumed
- **THEN** its declared scope is cleared before it runs again, without a further
  override

#### Scenario: A restarting step's scope is still confined
- **WHEN** a restarting step's declared scope resolves outside the command's
  containment root
- **THEN** clearing is refused, because every path the framework clears is
  containment-checked regardless of which mechanism clears it

#### Scenario: Progress and artifacts are readable on resume
- **WHEN** a resumed step needs to know where it stopped
- **THEN** the recorded progress and the registered artifact paths are available
  to it from the manifest

### Requirement: Resume
An explicit resume override SHALL permit writing into an occupied run directory,
and SHALL be refused when the recorded state is completed. Resuming SHALL skip
steps already recorded complete.

#### Scenario: Resuming an interrupted run
- **WHEN** an interrupted run is resumed
- **THEN** completed steps are skipped and the interrupted step runs again under
  its declared resume policy

#### Scenario: Starting a later step in a new invocation
- **WHEN** a run directory holds a persisted configuration and no work has run
- **THEN** resuming it starts the first step rather than refusing

#### Scenario: Resuming a completed run
- **WHEN** resume is requested against a completed run
- **THEN** it is refused, and starting from the same configuration is offered

### Requirement: Atomic artifact writes
Every framework-written artifact SHALL be written to a uniquely named temporary
file in the destination directory and renamed into place, so a reader never
observes a truncated or partially written artifact, and an interrupted write
leaves no residue.

#### Scenario: Interrupted write
- **WHEN** a write fails partway
- **THEN** the previous artifact is intact and no temporary file remains

### Requirement: The run manifest is a live record
A run directory SHALL hold exactly one manifest, recording the command, the state,
a timeline, the invocations, the computed identities, the canonical encoder
version, the registered artifacts, and any recorded best result. The manifest
SHALL be mutable during work through a single writer, flushing atomically on every
mutation, so an interrupted run still carries a useful record.

#### Scenario: Artifact registered before completion
- **WHEN** work registers an artifact and is then interrupted
- **THEN** the manifest names that artifact and the run is recorded interrupted

#### Scenario: Failed invocation
- **WHEN** work raises after initialization
- **THEN** the manifest records the failure and the provenance bundle remains

### Requirement: Run states derived from the timeline
The manifest SHALL carry one of five states — initialized, running, completed,
interrupted, failed — where interrupted means stopped by an external signal and
failed means work raised. The state SHALL always be derived from the last reserved
timeline event and never written independently. A project MAY append its own
timeline events but SHALL NOT write reserved event names or the state.

#### Scenario: Interrupted versus failed
- **WHEN** a run is stopped by a signal and another raises an exception
- **THEN** the first is interrupted and the second failed

#### Scenario: Project event
- **WHEN** a project records a domain event
- **THEN** it appears on the timeline and the state is unchanged

### Requirement: The project owns what counts as best
The framework SHALL record a best result supplied by the project — its epoch,
step and metrics — and SHALL NOT select it, rank it, or prescribe a metric
substrate.

#### Scenario: Best recorded
- **WHEN** a project records a best result
- **THEN** the manifest stores it verbatim and no metric is interpreted

### Requirement: Invocations and per-invocation configuration
The manifest SHALL record every invocation against a run directory, each with its
command, argument text, timestamp, computed configuration identity, and a
configuration index. Configuration indices SHALL be deduplicated on the canonical
bytes of the resolved configuration. Timeline events SHALL reference the
invocation that produced them.

#### Scenario: Repeated invocation with the same configuration
- **WHEN** an augmenting command runs twice with identical configurations
- **THEN** both invocations are recorded and both reference one configuration index

#### Scenario: Invocation with overrides
- **WHEN** an invocation supplies overrides changing the resolved configuration
- **THEN** a new configuration index is recorded and its bundle persisted beside
  the first

### Requirement: Config provenance bundle
A mutating command SHALL atomically persist the composed configuration, the
authoritative resolved configuration, and one artifact per computed hash, before
work begins; SHALL persist each deferred hash as it is completed during work; and
SHALL record the invocation in the manifest. The bundle for the first
configuration SHALL live at a fixed path and subsequent ones beneath an indexed
path.

#### Scenario: Deferred hash completed mid-run
- **WHEN** a deferred hash is completed forty minutes into a run
- **THEN** its artifact appears then, and the rest of the bundle was already present

#### Scenario: Completed invocation
- **WHEN** work completes
- **THEN** the manifest records its registered artifacts and identities

### Requirement: Recorded lineage for continued runs
Continuing from an existing run SHALL produce a new run directory and SHALL record
the parent run directory, its configuration identity, and the artifact continued
from. The framework SHALL NOT interpret whether the child restores training state
or only loads weights.

#### Scenario: Continuing a completed run
- **WHEN** a run is continued with an override
- **THEN** a new run directory is created whose manifest names its parent

### Requirement: One writer per mutable artifact
Every artifact that is rewritten over a run's life SHALL have exactly one writer.
A grouped operation SHALL write its own manifest from the parent rather than from
its children.

#### Scenario: Grouped run
- **WHEN** children of a grouped operation complete concurrently
- **THEN** the group manifest reflects every child without a lost update
