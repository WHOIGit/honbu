## ADDED Requirements

### Requirement: The sweep block is the single sweep definition
Sweep axes SHALL be declared in one top-level sweep block and nowhere else. A
field SHALL NOT carry its own sweep declaration, because a strict schema cannot
hold one: a field typed as an integer cannot also hold a list of integers without
being retyped as a union, which would admit a sweep specification into validation
and into every digest, and on a genuinely list-valued field the two readings are
indistinguishable.

The block SHALL be settable both in authored configuration and by ordinary
overrides, requiring no new grammar and no dedicated option — the axis container
SHALL be an open mapping in the root schema so that an override addressing a path
inside it needs no addition prefix.

#### Scenario: Axes authored in configuration
- **WHEN** a configuration carries a sweep block with axes
- **THEN** preparation expands them

#### Scenario: Axes supplied as overrides
- **WHEN** axes are supplied as overrides addressing paths inside the axis
  container
- **THEN** they are expanded identically to authored axes, with no addition prefix
  required

#### Scenario: A field-level sweep declaration is refused
- **WHEN** a configuration supplies a list where the schema declares a scalar
- **THEN** it fails strict validation, because sweeps are not expressed on the field

### Requirement: Axis normalization is recorded, not inferred
Axis keys SHALL normalize to flat dotted target paths before validation, whether
authored as flat dotted paths or arriving nested from overrides.
Each child SHALL record its own realized axis values in its configuration
directory, so the desugared form is legible without re-deriving it.

#### Scenario: Nested override normalizes to a target path
- **WHEN** an override addresses a nested path inside the axis container
- **THEN** it normalizes to the same target path as the equivalent flat dotted key

#### Scenario: Realized values are recorded per child
- **WHEN** a child is prepared
- **THEN** its configuration directory records the axis values that produced it

### Requirement: Cartesian and paired axes
A sweep SHALL support cartesian expansion, where the children are the product of
every axis, and paired expansion, where a group of parameters varies together as a
list of points. Paired groups SHALL compose with cartesian axes, contributing one
axis of length equal to the number of points.

Paired expansion SHALL be a distinct construct rather than an option on cartesian
expansion, because a product cannot express it: two parameters that must move
together yield the diagonal, and asking for the diagonal of a grid is asking for a
different operation.

#### Scenario: Cartesian product
- **WHEN** a sweep declares axes of length two and three
- **THEN** six children are prepared

#### Scenario: Paired group
- **WHEN** a sweep declares a paired group of two points
- **THEN** two children are prepared, not four

#### Scenario: Paired group inside a product
- **WHEN** a paired group of two points is combined with a cartesian axis of
  length three
- **THEN** six children are prepared

### Requirement: A sweep declares the value that distinguishes its children
A sweep SHALL declare the configuration path whose value identifies a child, and
preparation SHALL refuse when two children share that value. The gate SHALL be
checked across all children before any child directory is written, so a collision
is reported at setup rather than discovered when a later child overwrites an
earlier one.

The declared value SHALL depend only on the configuration and not on the
invocation, and the framework SHALL verify this rather than trust it: a value that
is freshly generated per invocation can never collide and so gates nothing, while
appearing to pass. Verification SHALL be by resolving one configuration under two
invocation contexts and requiring the value to be stable.

The declared value SHALL appear in the child output template. This makes distinct
identifiers imply distinct output directories, so the semantic check and the
protection against children overwriting one another cannot disagree.

A refusal SHALL name the colliding children and the axes that failed to
distinguish them, and SHALL suggest adding the child index or a realized axis
value to the template.

#### Scenario: Colliding identifiers
- **WHEN** two expanded children resolve to the same declared identifier
- **THEN** preparation refuses before writing anything, naming both children

#### Scenario: Invocation-derived identifier
- **WHEN** the declared identifier is freshly generated per invocation
- **THEN** preparation refuses, because such a value cannot detect a collision

#### Scenario: Identifier absent from the output template
- **WHEN** the declared identifier does not appear in the child output template
- **THEN** preparation refuses, because distinct children could share a directory

#### Scenario: Identifier reached through a template
- **WHEN** the declared identifier is a template-valued field
- **THEN** it is checked after rendering, against the value that will be used

### Requirement: A prepared child is indistinguishable from a hand-run configuration
The sweep block SHALL be consumed by preparation and removed from each child's
configuration, so a child's digests are identical to those of the same
configuration invoked directly. Sweep membership SHALL be recorded in the child's
manifest — the sweep identity and the child's coordinate — never in its
configuration.

Otherwise every child's configuration identity would incorporate the whole sweep
definition, and a child could not be compared against a baseline produced by hand.

#### Scenario: Child digest matches a direct invocation
- **WHEN** a child's configuration is also invoked directly with the same values
- **THEN** both produce the same configuration digest

#### Scenario: Membership recorded outside the configuration
- **WHEN** a child is prepared as part of a sweep
- **THEN** its manifest records the sweep identity and its coordinate, and its
  configuration carries no sweep block

### Requirement: Preparation materializes every child and runs nothing
Preparation SHALL expand the matrix, resolve every child, check the gate, then
write every child's run directory in the initialized state without performing
work. Preparation SHALL be idempotent: re-preparing SHALL leave an existing child
untouched rather than clearing it.

A producing command SHALL refuse an active sweep block, directing the caller to
preparation, so that one command has one target.

#### Scenario: Prepared but not run
- **WHEN** a sweep is prepared
- **THEN** every child holds its composed configuration, resolved configuration and
  resolved hashes, and every child's state is initialized

#### Scenario: Re-preparing an existing sweep
- **WHEN** preparation runs again over a sweep whose children exist
- **THEN** existing children are left as they are, including completed ones

#### Scenario: Training command given a sweep
- **WHEN** a producing command is invoked with an active sweep block
- **THEN** it is refused, directing the caller to prepare the sweep first

### Requirement: Sweep state is derived from the children's manifests
The sweep index SHALL be immutable after preparation and SHALL record no progress.
A child's state SHALL be held solely in that child's own manifest, and the sweep's
state SHALL be the aggregate, computed on read. Nothing but a child SHALL write a
child's manifest.

Execution SHALL therefore be resumable with no driver-side state: a child recorded
complete SHALL be skipped, and a child in any resumable state SHALL be resumed.
Stopping execution at any point and resuming later SHALL lose no completed work and
SHALL repeat none.

#### Scenario: Interrupted sweep resumes
- **WHEN** execution is stopped partway and started again
- **THEN** completed children are skipped, the interrupted child resumes, and the
  remainder run

#### Scenario: Child interrupted mid-step
- **WHEN** a child is interrupted during a step whose policy is to continue
- **THEN** resuming that child preserves its prior output and its recorded progress

#### Scenario: Scheduler timeout
- **WHEN** a child is killed by a scheduler and left recorded as running
- **THEN** it is treated as resumable and picked up by the next execution pass

#### Scenario: State is not cached
- **WHEN** the sweep's status is requested
- **THEN** it is computed from the children's manifests rather than read from a
  sweep-level progress record

### Requirement: Uniform child invocation
Every prepared child SHALL be executable by identifying its run directory and
requesting resume, regardless of whether it has ever started. This SHALL hold for
any command shape, so that executing a sweep requires no sweep-specific entry point
and an external scheduler can run children directly.

#### Scenario: Never-started child
- **WHEN** a child in the initialized state is invoked with resume
- **THEN** it runs from the beginning

#### Scenario: External scheduler runs children
- **WHEN** children are submitted independently by an external job scheduler
- **THEN** each records its own state and the sweep's aggregate state stays correct

### Requirement: The sweep directory
A sweep SHALL resolve its own output block, peer to a run's output blocks, holding
the sweep index and sweep-level artifacts. Its configuration directory SHALL hold
the sweep-level composed and resolved configurations and the index; sweep-level
metrics, figures and promoted artifacts SHALL live in their own leaves.

The sweep index SHALL record, for each child, its coordinate, its declared
identifier, its realized axis values, its run directory, and its configuration
identity where one exists. Reporting SHALL locate children through the index and
SHALL NOT discover them by scanning directories, so a child that was moved or
removed is reported as missing rather than silently omitted.

Sweep-level reporting SHALL be disableable, and disabling it SHALL NOT prevent
preparation or execution.

#### Scenario: Index locates children
- **WHEN** reporting runs
- **THEN** every child is read at the location the index records

#### Scenario: Missing child
- **WHEN** a recorded child's run directory no longer exists
- **THEN** reporting names it as missing rather than omitting it

#### Scenario: Reporting disabled
- **WHEN** sweep-level reporting is disabled
- **THEN** preparation and execution proceed and only collection is skipped

### Requirement: Sweep identity
A sweep SHALL have an identity, and a digest over its definition — the base
configuration and the axes — computed under the same exclusions as any other
digest, so that generated identities and output locations do not enter it. Two
sweeps differing only in output location SHALL share a definition digest.

Children SHALL carry the sweep identity as provenance, so results produced within
a sweep are attributable to it.

#### Scenario: Output location excluded
- **WHEN** two sweeps differ only in where they write
- **THEN** their definition digests are equal

#### Scenario: Axis change alters the digest
- **WHEN** an axis gains a value
- **THEN** the definition digest differs

### Requirement: Execution backends are adapters, not capabilities
Sequential local execution SHALL require no optional dependency. Job submission
and search-driven sweeping SHALL be provided as adapters over independent
libraries declared as optional dependencies, and SHALL NOT be reached through the
compositor's plugin interfaces.

A search-driven sweep SHALL read its objective from a child's recorded best
metrics rather than from a work function's return value, so that a proposal
outlives the process that made it and a child evaluated hours later in a separate
job can still report back.

#### Scenario: Local execution needs nothing extra
- **WHEN** a sweep is executed locally and sequentially
- **THEN** no optional dependency is required

#### Scenario: Objective read from the record
- **WHEN** a search-driven sweep evaluates a completed child
- **THEN** the objective is read from that child's recorded best metrics

#### Scenario: Search state survives a restart
- **WHEN** a search-driven sweep is stopped and resumed
- **THEN** previously evaluated children are not re-proposed

### Requirement: Declared objective
A results block SHALL declare an objective — a metric key and a direction — used
to rank runs; the framework SHALL rank generically without domain knowledge.

#### Scenario: Rank by objective
- **WHEN** a set of completed runs is ranked
- **THEN** the objective's metric and direction determine the order

### Requirement: Parameter sweeps
A sweep SHALL declare a base configuration, axes as configuration paths with
values, a strategy, an output template, and an objective; the framework SHALL
expand the axes, resolve each child, assign child identities, record a sweep
index, and group results by configuration identity — without knowing what the
project's work function trains.

#### Scenario: Axis expansion
- **WHEN** a sweep declares axes and a grid strategy
- **THEN** one child run is resolved per combination and recorded in the index

#### Scenario: Invalid combination
- **WHEN** an expanded combination fails strict validation
- **THEN** it is recorded as skipped and preparation proceeds

### Requirement: N-way run comparison
Comparison SHALL check the gate hashes for the requested operation across a set
of runs, report the residual configuration differences grouped by contributing
hash source, and rank by the objective; a sweep's per-axis rollup and ad-hoc
comparison SHALL use the same engine. The identity that gates comparison SHALL be
named by the caller rather than assumed, since a project may declare none.

#### Scenario: Not comparable
- **WHEN** two runs differ on a hash that gates comparison
- **THEN** the engine reports them as not comparable, naming the hash

#### Scenario: Residual difference
- **WHEN** two runs differ only in fields no behavioral hash reads
- **THEN** the difference is reported as residual and behavioral identities stay equal

#### Scenario: No configuration identity declared
- **WHEN** runs carry no configuration digest because the project declared none
- **THEN** comparison reports the residual differences and gates on nothing

### Requirement: Version-aware reconciliation
Comparison SHALL gate on per-hash recipe version, never the framework version.
On a recipe-version difference it SHALL reconcile configuration identity by
diffing the stored resolved configurations and domain identities by re-hashing
stored source objects; when reconciliation is impossible it SHALL warn and
proceed for advisory operations and refuse for operations that produce a fused
artifact.

#### Scenario: Minor bump stays comparable
- **WHEN** two runs were produced under framework versions that left every
  relevant hash recipe unchanged
- **THEN** they compare with no version warning

#### Scenario: Ensemble refuses on unreconcilable mismatch
- **WHEN** an ensemble's gating identities cannot be reconciled across recipe
  versions
- **THEN** the ensemble is refused rather than produced

### Requirement: Ensemble assessment
Ensemble assessment SHALL apply the stricter gate set, invoke a project-supplied
combine work function, and assess the result against the objective.

#### Scenario: Ensemblable set
- **WHEN** a set passes the ensemble gate set
- **THEN** the combine work function is invoked and the result is assessed by the objective
