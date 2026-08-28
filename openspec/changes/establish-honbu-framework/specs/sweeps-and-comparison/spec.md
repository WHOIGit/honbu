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

### Requirement: The sweep block declares a gate, axes, and covariant axes
A sweep block SHALL carry three things: a **gate** naming the configuration path
that distinguishes children, an **axes** mapping of target path to values, and an
optional **covariant_axes** mapping of label to options, where each option assigns
several paths at once.

Every axis SHALL contribute exactly one dimension to the cartesian product,
including a covariant axis, whose length is its number of options. Children SHALL
be the product of every dimension.

A covariant axis SHALL be a distinct construct rather than an option on an ordinary
axis, because a product cannot express co-variance: two parameters that must move
together yield a diagonal, and asking for the diagonal of a grid is a different
operation. Ordinary axes and covariant axes SHALL compose freely.

#### Scenario: Cartesian product
- **WHEN** a sweep declares axes of length two and three
- **THEN** six children are prepared

#### Scenario: Covariant axis
- **WHEN** a sweep declares a covariant axis of two options
- **THEN** two children are prepared, not one per assigned path

#### Scenario: Covariant axis inside a product
- **WHEN** a covariant axis of two options is combined with an axis of length three
- **THEN** six children are prepared, and every child's covarying paths come from
  one option

### Requirement: Group axes and covariant axes reach different places
An axis whose values name configuration-group options SHALL select whole group
files, applied during composition. A covariant axis SHALL assign dotted paths
directly, applied after composition.

The two SHALL NOT be treated as interchangeable, because a group file's package
path is static: it cannot target a location whose key the configuration chose, such
as a field inside a mapping keyed by a name. Any path below such a container is
therefore reachable only by an ordinary or covariant axis, and the framework SHALL
document this rather than leaving it to be discovered.

A configuration-group axis SHALL require that the base configuration does not also
supply values at the group's package path. A group is merged rather than
substituted, so a base that defines the same location yields the union of both and
an ablation silently fails to ablate.

#### Scenario: Group axis selects whole files
- **WHEN** an axis's values name options of a configuration group
- **THEN** each child composes with that option selected

#### Scenario: Group axis cannot reach inside a container
- **WHEN** a bundle of settings must be applied at a configuration-chosen key
- **THEN** it is expressed as a covariant axis, because no group file can name that
  package path

#### Scenario: Base configuration shadows a swept group
- **WHEN** the base configuration supplies values at a swept group's package path
- **THEN** preparation refuses, naming the path, because merging would give every
  child the union rather than the selected option

### Requirement: Axis paths are validated against the schema and the configuration
Every axis and covariant-axis path SHALL be validated at preparation, before any
child is resolved, by two checks that catch different mistakes.

The **schema** check SHALL confirm the path names a real field, traversing a
mapping wherever the following segment is a concrete key, so that a path below a
configuration-keyed container is validatable at all. A container step SHALL be
traversable wherever it occurs, not only at the first one.

The **data** check SHALL confirm every concrete container key on the path is
present in the composed configuration. This is not redundant: a mapping's schema
admits any key, so a misspelled container key passes the schema check and would
otherwise fabricate a new entry — failing later, per child, as an invalid
combination, which is the wrong error at the wrong time.

A path failing either check SHALL be refused, naming the path and which check
failed. It SHALL NOT be recorded as a skipped combination; skipping is for
combinations a project validator rejects, and a bad path would skip every child
while reporting a sweep that merely found nothing.

#### Scenario: Misspelled leaf
- **WHEN** an axis path names a field the schema does not declare
- **THEN** preparation refuses, naming the path

#### Scenario: Misspelled container key
- **WHEN** an axis path names a container key absent from the composed
  configuration
- **THEN** preparation refuses, rather than creating an entry under that key

#### Scenario: Path below a container is valid
- **WHEN** an axis path descends through a concrete mapping key into a nested block
- **THEN** it validates, and the axis expands

#### Scenario: A bad path is not a skipped combination
- **WHEN** an axis path is invalid
- **THEN** preparation refuses outright rather than preparing zero children

### Requirement: The sweep block is well-formed before it is expanded
Three properties SHALL hold of a sweep block, checked at preparation:

Every option of a covariant axis SHALL assign the same set of paths. An option
omitting a path its siblings assign would silently inherit the base configuration's
value, which is a hole wearing the shape of an axis.

No path SHALL be claimed twice across the whole block — not by two axes, not by an
axis and a covariant axis, not by two covariant axes. Two claims on one path mean
one silently wins per child.

A covariant axis SHALL declare at least two options. One option is a constant, not
an axis, and belongs in the base configuration where it is visible.

#### Scenario: Ragged covariant options
- **WHEN** one option of a covariant axis omits a path its siblings assign
- **THEN** preparation refuses, naming the path and the option

#### Scenario: Path claimed twice
- **WHEN** a path appears in an axis and also in a covariant axis
- **THEN** preparation refuses, naming the path and both claimants

#### Scenario: Single-option covariant axis
- **WHEN** a covariant axis declares one option
- **THEN** preparation refuses, directing the value to the base configuration

### Requirement: An override may not contest a swept path
An override addressing a path the sweep block sweeps SHALL be refused, directing
the caller to express it as an axis. Such an override is otherwise a no-op — the
axis assigns that path per child regardless — so accepting it would silently
discard what the caller asked for.

An override addressing any other path SHALL compose normally and join the base
configuration for every child, which is how a sweep is re-run against a different
input or output location.

Overrides addressing the sweep block itself SHALL redefine the sweep.

#### Scenario: Override contests an axis
- **WHEN** an override assigns a path that the sweep block declares as an axis
- **THEN** it is refused, naming the path and the axis form to use instead

#### Scenario: Override outside the swept set
- **WHEN** an override assigns a path no axis claims
- **THEN** it applies to every child

#### Scenario: Override redefines the sweep
- **WHEN** an override addresses the sweep block
- **THEN** it changes the sweep definition rather than being refused

### Requirement: An axis overrides the composed value, and says so
An axis SHALL win over a value the composed configuration already sets at that
path, for every child, and preparation SHALL warn once per such axis — naming the
axis and the value it displaces.

There SHALL be no policy governing this. A stricter alternative, refusing until the
base configuration stops setting the path, was considered and rejected: it is
incompatible with configuration-group axes, whose package path is *always* set by
the base defaults list; the mistake it chiefly guarded against — a misspelled path
— is caught precisely by axis-path validation; and its remedy pressures an author
to delete a default that is meaningful when the configuration is run alone.

The warning SHALL be emitted once per axis rather than once per child, so a large
sweep does not bury it.

#### Scenario: Axis displaces a base value
- **WHEN** an axis assigns a path the composed configuration sets
- **THEN** every child takes the axis value, and one warning names the displaced one

#### Scenario: Warning does not scale with the sweep
- **WHEN** a sweep of many children displaces one base value
- **THEN** the warning is emitted once

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
A child's state SHALL be held solely in that child's own manifest, and the sweep's
state SHALL be the aggregate. Nothing but a child SHALL write a child's manifest,
so the two can never disagree.

The sweep manifest SHALL hold two zones with different rules. The **index** —
children, coordinates, gate values, realized axis values, run directories — SHALL
be immutable after preparation. The **progress** zone SHALL be a cache: every value
in it SHALL be derivable by rescanning the children, and it SHALL carry the time it
was last refreshed.

The progress zone SHALL never be authoritative. It exists so a human can read a
sweep's state at a glance without invoking a tool, which is worth having; but a
child run by an external scheduler updates its own manifest and nothing else, so
the cache is stale exactly when it is most wanted. Recording the refresh time is
therefore not decoration — without it the file states a count as though it were
current. Any operation reporting sweep state SHALL refresh before reporting.

Execution SHALL be resumable with no driver-side state: a child recorded complete
SHALL be skipped, and a child in any resumable state SHALL be resumed. Stopping
execution at any point and resuming later SHALL lose no completed work and SHALL
repeat none. Deleting the progress zone SHALL NOT change what execution does.

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

#### Scenario: Progress is readable without a tool
- **WHEN** a sweep has been executed in part
- **THEN** its manifest records each child's last-known state and the time that
  record was refreshed

#### Scenario: Cache is rebuilt, never trusted
- **WHEN** the progress zone is deleted or disagrees with the children
- **THEN** rescanning restores it, and no operation's behavior changed

#### Scenario: Children run outside the driver
- **WHEN** children are run by an external scheduler that updates only their own
  manifests
- **THEN** the sweep's progress zone is stale until refreshed, and its recorded
  refresh time says so

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
