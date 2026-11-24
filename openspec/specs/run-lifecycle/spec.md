# run-lifecycle Specification

## Purpose

Defines the command-driven run lifecycle: lifecycle-only command declarations, fixed-order side-effect-free resolution, pure output-path templating, portable and absolute resolved views, scope-confined collision safety, declared steps and resumability, atomic provenance bundles, forked run lineage, and the single-writer live run manifest.

## Requirements

### Requirement: Declared commands own their write scope
A project SHALL declare each command with lifecycle and schema metadata only — a
name tuple, producing or augmenting mode, target/scope paths, hashes, steps,
completion set, `advances_run`, accepted `config_sources`, and optional default
`fork_artifact` — with no CLI option or positional declarations. Declared dotted
paths SHALL be validated at registry construction, and a targetless command SHALL
be structurally incapable of clearing anything.

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
command; assign identities derived from those hashes; render template-valued fields;
render the command's target template; resolve output leaves against the target;
revalidate. No phase SHALL depend on a later phase.

Template-valued fields SHALL render before the target, so an output directory may
reference one.

#### Scenario: Identity feeds a path template
- **WHEN** a target template references a configuration identity
- **THEN** the identity is computed before the template is rendered

#### Scenario: Leaf anchored to the rendered target
- **WHEN** an output leaf is relative
- **THEN** it resolves against the target directory rendered in the prior phase

### Requirement: Command-aware side-effect-free resolution
Resolution SHALL be command-aware, perform no filesystem writes, and return its
warnings rather than emitting them. It SHALL accept optional `TemplateContext`
(minting via `TemplateContext.now()` when omitted), explicit `sweep_id`, and
`existing_target` for augmenting commands; when `replay=False`, it SHALL refuse
non-null authored `RESOLVED_ONLY` fields and active sweep blocks.

#### Scenario: Read-only inspection without inputs
- **WHEN** a configuration is resolved for inspection with its inputs absent
- **THEN** configuration identity and active output paths resolve and
  input-reading identities are reported as deferred

#### Scenario: Active sweep block refused directly in resolution
- **WHEN** `resolve()` is called on a composed configuration carrying active sweep
  axes
- **THEN** resolution refuses and directs the caller to prepare the sweep instead

### Requirement: Resolution and run are separate objects
Resolution SHALL return a pure object exposing both configuration views, computed
hash entries, deferred names, resolved target, and warnings, carrying no write
methods. The lifecycle SHALL wrap it in a `Run` once prepared, owning the manifest,
all writes, `run.warnings` (preserving resolution and target warnings), and
`run.hash()` / `run.hash_entry()` readers symmetric with `run.artifact()`.

#### Scenario: Inspection cannot write
- **WHEN** a configuration is resolved for read-only inspection
- **THEN** the returned object offers no operation that touches the filesystem

#### Scenario: Work writes through the run
- **WHEN** work registers an artifact or completes a deferred hash
- **THEN** it does so through the run object, which is the single writer

#### Scenario: Reading resolved and deferred hashes from the run
- **WHEN** work calls `run.hash(name)` or `run.hash_entry(name)` for a resolved
  hash or a completed deferred hash (including across `--resume` and fanned
  `<name>.<key>` entries)
- **THEN** it returns the persisted digest or `HashEntry`, or `None` if uncompleted

#### Scenario: Target warnings survive onto the run
- **WHEN** target preparation produces a warning (such as a case collision)
- **THEN** `run.warnings` includes both resolution warnings and target warnings

### Requirement: Revalidation re-runs project validators
Because the final phase revalidates the tree, every project model validator SHALL
run a second time against a tree whose identities are assigned and whose paths are
absolute. A project validator SHALL therefore hold for a resolved tree as well as
a composed one.

#### Scenario: Validator that only holds before resolution
- **WHEN** a project validator asserts something true only of an unresolved tree
- **THEN** it passes composition and fails revalidation, rather than failing silently

### Requirement: A target template may not reference a deferred value
A command's target template SHALL NOT depend on a deferred or unresolved hash.
Input-reading (`reads_inputs=True`) hashes SHALL be prevented structurally by
forbidding `FromHash` bindings on them at construction; if a `reads_inputs=False`
hash returns `None` during resolution, referencing its `FromHash` field in a
target template SHALL be refused before rendering.

#### Scenario: Deferred hash prevented structurally
- **WHEN** a schema attempts to bind a `reads_inputs=True` hash via `FromHash`
- **THEN** identity validation fails at construction before any template can
  reference it

#### Scenario: Dynamically absent hash in target template
- **WHEN** a `reads_inputs=False` hash returns `None` for a configuration and the
  target template references its `FromHash` field
- **THEN** resolution refuses before the target is rendered, naming the unassigned
  field rather than rendering null into a directory name

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

#### Scenario: Misspelled template function or bad format spec
- **WHEN** a template uses a suffix that is neither a registered function nor a
  valid Python format specifier (such as `{experiment.name:slugg}`)
- **THEN** rendering raises `TemplateError` as a user-facing refusal rather than
  an uncaught `ValueError`

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

#### Scenario: Schema-defaulted leaf overridden
- **WHEN** a schema defines a default `dir` (or `dir_template`) on an `OutputLeaf`
  and the caller authors the other field without nulling the default
- **THEN** Phase 7 checks authored fields (matching Phase 6), renders cleanly,
  collects leaf template warnings, and enforces path-length limits

#### Scenario: Augmenting command re-renders templated leaf
- **WHEN** an augmenting command replays a prior run (`--resolved`) with overrides
  affecting a templated leaf in its `scope` and does not pin a literal `leaf.dir`
- **THEN** Phase 7 re-renders `leaf.dir_template` so the augmenting output lands
  in a fresh leaf rather than colliding on the prior run's rendered leaf dir

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
by default. Under an explicit override it SHALL clear only that scope, strictly
within the declared containment root. Refusals SHALL describe the occupant using
flag-agnostic wording by default and SHALL accept an optional CLI hint so a
project can name its own flags.

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

#### Scenario: Clobber and resume are mutually exclusive
- **WHEN** a caller passes both `clobber=True` and `resume=True`, or passes
  `resume=True` to an augmenting command
- **THEN** opening the run refuses outright rather than silently ignoring a flag

#### Scenario: Disabled leaf and file-at-leaf scope safety
- **WHEN** an augmenting command's scope includes a disabled `OutputLeaf`
  (`enabled=False`), or a leaf path exists as a regular file
- **THEN** disabled leaves are neither created nor cleared, and a file at an
  enabled leaf is refused without `clobber` (or unlinked with `clobber`)

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
An explicit resume override SHALL permit writing into an occupied run directory
in any resumable state and SHALL be refused when completed. Resuming SHALL skip
every step already satisfied (`completed` or `not_applicable`), SHALL preserve
the run's recorded `run_id` when not overridden, and SHALL refuse behavioral
`config_hash` drift by default (`allow_config_drift=False`), directing callers to
fork or clobber instead.

#### Scenario: Resuming an interrupted run
- **WHEN** an interrupted run is resumed
- **THEN** completed and not-applicable steps are skipped via `run.skip()` and
  the interrupted step runs again under its declared resume policy

#### Scenario: Starting a later step in a new invocation
- **WHEN** a run directory holds a persisted configuration and no work has run
- **THEN** resuming it starts the first step rather than refusing

#### Scenario: Resuming a partial run
- **WHEN** a run left partial by a previous invocation is resumed
- **THEN** its outstanding required steps run and the run reaches completed

#### Scenario: Resuming a completed run
- **WHEN** resume is requested against a completed run
- **THEN** it is refused, and starting from the same configuration is offered

#### Scenario: Resuming with changed behavioral configuration refused
- **WHEN** resume resolves to a different `config_hash` than the interrupted run's
  recorded `config_hash` without `allow_config_drift=True`
- **THEN** resume refuses, directing the caller to fork from the run or clobber
  and start fresh

#### Scenario: Resuming from default config preserves run_id
- **WHEN** an interrupted run is resumed via default config on a deterministic
  template without overriding `run_id`
- **THEN** the run's recorded `run_id` is preserved and configuration index 1 is
  reused rather than allocating a duplicate `config/2/` bundle

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
- **THEN** the manifest records the failure (`kind` and `message` on both
  `failure` and the timeline event) and the provenance bundle remains

#### Scenario: Stale failure cleared on resumed run
- **WHEN** a previously failed or interrupted run resumes and finishes
- **THEN** top-level `manifest["failure"]` is cleared to null while the historical
  failure event remains on the append-only timeline

#### Scenario: Incremental progress merge and full replacement
- **WHEN** work calls `run.record_progress(...)` repeatedly with different keys
- **THEN** keys merge into `manifest["progress"]` rather than erasing earlier
  keys, while `run.set_progress(...)` replaces the progress mapping wholesale

### Requirement: Run states derived from the timeline
The manifest SHALL carry one of six states — initialized, running, partial,
completed, interrupted, failed — where interrupted means stopped by an external
signal, failed means work raised, and partial means work returned cleanly with
required steps outstanding. The state SHALL always be derived from the last
reserved timeline event and never written independently. A project MAY append its
own timeline events but SHALL NOT write reserved event names or the state.

Every state but completed SHALL be resumable.

#### Scenario: Interrupted versus failed
- **WHEN** a run is stopped by a signal and another raises an exception
- **THEN** the first is interrupted and the second failed

#### Scenario: Project event
- **WHEN** a project records a domain event
- **THEN** it appears on the timeline and the state is unchanged

#### Scenario: State derived from timeline on load
- **WHEN** `Manifest.load` opens a manifest (default `derive_state=True`)
- **THEN** `state` is derived in memory by scanning backward to the last reserved
  timeline event, with `derive_state=False` available to opt out

### Requirement: A producing command declares what completion means
A producing command SHALL declare which of its steps constitute a complete run,
defaulting to all of them. On a work function returning normally, the run SHALL be
recorded completed when every required step is recorded complete, and partial
otherwise.

Returning SHALL NOT by itself mean completion. Treating it as completion makes
running part of a stepped command indistinguishable from running all of it, leaves
a deliberately partial run in the one state that cannot be resumed, and forces a
framework flag to exist for the single case of doing no work at all.

A project declaring an optional step SHALL be able to omit it from the required
set, so an unrun evaluation does not hold a trained run open forever.

A required step MAY instead be declared **not applicable** to a particular run,
with a reason, and SHALL then count as satisfied. This exists so that silence keeps
meaning something is wrong: a step that neither ran nor was declared inapplicable
leaves the run partial, which makes forgetting loud and makes deliberate omission
recorded and auditable. Permanently removing such a step from the required set
would instead make both cases silent and identical.

A step already recorded complete SHALL NOT be declarable not applicable, and a step
SHALL be counted satisfied when it is recorded complete in *any* invocation of the
run, not merely the current one.

Completion describes the **run**, not the invocation: a step recorded complete by
one command counts for the run regardless of which command recorded it.

#### Scenario: All required steps ran
- **WHEN** a work function returns having completed every required step
- **THEN** the run is recorded completed

#### Scenario: Some required steps outstanding
- **WHEN** a work function returns having completed only its first step
- **THEN** the run is recorded partial and remains resumable

#### Scenario: Optional step never ran
- **WHEN** a command declares a step outside its required set and does not run it
- **THEN** the run is still recorded completed

#### Scenario: Required step declared not applicable
- **WHEN** work declares a required step not applicable to this run, with a reason
- **THEN** the run reaches completed and the timeline records the omission and its
  reason

#### Scenario: Required step silently missing
- **WHEN** a required step neither runs nor is declared not applicable
- **THEN** the run is recorded partial, so forgetting is distinguishable from
  deliberate omission

#### Scenario: Step satisfied by an earlier invocation
- **WHEN** a step recorded complete in a previous invocation is skipped on resume
- **THEN** it still counts toward completion

### Requirement: A command declares whether running it advances the run
A command SHALL declare whether its execution constitutes work on the run. One that
does SHALL record the lifecycle events and reach completed or partial as above. One
that does not SHALL record its own timeline events and its own failure, and SHALL
leave the run's state exactly as it found it. An augmenting command SHALL never
advance a run.

This SHALL be a declaration of whether, not of which state. Because a state is
always derived from the last reserved timeline event, a command naming a target
state would either break that derivation or require an event per nameable state —
and would let a command that performed no work declare the run complete.

Two shapes follow. A command that prepares a run for later work declares no steps
and does not advance it, so the run is left initialized and stays resumable —
which is why no framework option is needed to prepare a run in advance. And a
failed augmentation cannot un-complete a run: a failed evaluation of a finished
training run has not un-trained it, and recording the run failed would destroy the
fact that its artifacts are complete and usable.

#### Scenario: Preparing a run without working it
- **WHEN** a command that does not advance the run creates a run directory and
  returns
- **THEN** the run is left initialized, resumable, with no claim that work occurred

#### Scenario: Augmenting a completed run
- **WHEN** an augmenting command succeeds against a completed run
- **THEN** the run remains completed and its timeline records the invocation

#### Scenario: Augmentation fails
- **WHEN** an augmenting command raises against a completed run
- **THEN** the failure is recorded and the run remains completed

#### Scenario: Augmenting twice against different outputs
- **WHEN** a run is evaluated twice against different inputs, each writing its own
  templated leaf
- **THEN** both results are kept and the run remains completed

#### Scenario: A non-advancing command cannot claim completion
- **WHEN** a command that does not advance the run runs to completion
- **THEN** no lifecycle state event is recorded for it

### Requirement: The project owns what counts as best
The framework SHALL record a best result supplied by the project — its epoch,
step and metrics — and SHALL NOT select it, rank it, or prescribe a metric
substrate.

#### Scenario: Best recorded
- **WHEN** a project records a best result
- **THEN** the manifest stores it verbatim and no metric is interpreted

### Requirement: Invocations and per-invocation configuration
The manifest SHALL record every invocation against a run directory, each with its
command, argument text, timestamp, computed configuration digest (looked up by a
parameterized hash name defaulting to `config_hash`), and a configuration index
deduplicated on the canonical bytes of the resolved configuration. Timeline events
SHALL reference the invocation that produced them.

#### Scenario: Repeated invocation with the same configuration
- **WHEN** an augmenting command runs twice with identical configurations
- **THEN** both invocations are recorded and both reference one configuration index

#### Scenario: Invocation with overrides
- **WHEN** an invocation supplies overrides changing the resolved configuration
- **THEN** a new configuration index is recorded and its bundle persisted beside
  the first

#### Scenario: Augmenting overrides preserve producing run hashes
- **WHEN** an augmenting command runs with overrides (`config_index > 1`)
- **THEN** its own hashes persist in `config/<N>/hashes/` and
  `invocations[i]["config_hash"]`, while top-level `manifest["hashes"]` keeps the
  producing run's existing digests intact and only adds newly introduced hash
  names

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

### Requirement: Recorded lineage for forked runs
Forking from an existing run SHALL produce a new run directory and SHALL record
the parent run directory, its configuration digest (parameterized hash name), the
selected `artifact_key`, and its `artifact` path via public `parent_record`. A
verified parent artifact SHALL be required, selectable at call time with an
optional command default. Honbu SHALL NOT interpret weight-vs-state restoration.

#### Scenario: Forking a completed run
- **WHEN** a run is forked with an override and a registered parent artifact key
- **THEN** a new run directory is created whose manifest records `parent` with
  `run_dir`, `config_hash`, `artifact_key`, and `artifact`

#### Scenario: Choosing among multiple parent artifacts
- **WHEN** a parent registers `best_checkpoint` and `last_checkpoint` and the
  caller selects `best_checkpoint`
- **THEN** `parent_record` verifies and records `best_checkpoint` rather than
  forcing a single hardcoded key

### Requirement: One writer per mutable artifact
Every artifact that is rewritten over a run's life SHALL have exactly one writer.
A grouped operation SHALL write its own manifest from the parent rather than from
its children.

#### Scenario: Grouped run
- **WHEN** children of a grouped operation complete concurrently
- **THEN** the group manifest reflects every child without a lost update
