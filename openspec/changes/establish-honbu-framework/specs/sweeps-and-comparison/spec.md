## ADDED Requirements

### Requirement: Declared objective
A results block SHALL declare an objective — a metric key and a direction — used
to rank runs; the framework SHALL rank generically without domain knowledge.

#### Scenario: Rank by objective
- **WHEN** a set of completed runs is ranked
- **THEN** the objective's metric and direction determine the order

### Requirement: Parameter sweeps
A sweep SHALL declare a base configuration, axes as configuration paths with
values, a strategy, an output template, and an objective; the framework SHALL
expand the axes, resolve each child, assign child identities, run each through
the lifecycle calling the project work function, record a sweep manifest, and
group results by configuration identity — without knowing what the work function
trains.

#### Scenario: Axis expansion
- **WHEN** a sweep declares axes and a grid strategy
- **THEN** one child run is resolved per combination and recorded in the manifest

#### Scenario: Invalid combination
- **WHEN** an expanded combination fails strict validation
- **THEN** it is recorded as skipped and the sweep proceeds

### Requirement: N-way run comparison
Comparison SHALL check the gate hashes for the requested operation across a set
of runs, report the residual configuration differences grouped by contributing
hash source, and rank by the objective; a sweep's per-axis rollup and ad-hoc
comparison SHALL use the same engine.

#### Scenario: Not comparable
- **WHEN** two runs differ on a hash that gates comparison
- **THEN** the engine reports them as not comparable, naming the hash

#### Scenario: Residual difference
- **WHEN** two runs differ only in fields no behavioral hash reads
- **THEN** the difference is reported as residual and behavioral identities stay equal

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
