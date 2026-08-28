## ADDED Requirements

### Requirement: Comparison operands are configuration references
Comparison SHALL accept two or more operands, each a reference to a configuration
of the same kinds the framework already composes from — the default root, an
authored file, a prior run's composed or resolved configuration, or a run
directory — and each SHALL accept its own overrides.

Every operand SHALL be normalized to a composed mapping before comparison, so
operands of different kinds are comparable. Comparing files before composition is
what a text diff already does; the question comparison answers is what they mean
once merged.

An operand naming a run directory SHALL resolve to that run's recorded
configuration without the caller having to know the bundle's layout.

#### Scenario: Mixed operand kinds
- **WHEN** an authored file and a prior run's resolved configuration are compared
- **THEN** both normalize and the comparison reports their differences

#### Scenario: One configuration against itself with overrides
- **WHEN** the same source is given twice, the second with overrides
- **THEN** the comparison reports exactly what the overrides changed

#### Scenario: Run directory as an operand
- **WHEN** an operand names a run directory
- **THEN** its recorded configuration is located without a path into the bundle

#### Scenario: Group fragment
- **WHEN** an operand names a configuration-group fragment that is not a whole
  configuration
- **THEN** it is comparable only at the literal level, because it cannot validate

### Requirement: N-way comparison
Comparison SHALL support any number of operands, reporting one entry per differing
path carrying each operand's value at that path, with absence distinguished from a
null value. Two-operand comparison SHALL be the degenerate case of the same
operation rather than a separate one.

#### Scenario: Three configurations
- **WHEN** three configurations are compared
- **THEN** each differing path reports all three values

#### Scenario: Present-and-null versus absent
- **WHEN** one operand omits a path another sets to null
- **THEN** the two are reported as differing

#### Scenario: Agreement is not reported
- **WHEN** every operand holds the same value at a path
- **THEN** that path does not appear in the result

### Requirement: Comparison levels
Comparison SHALL be performed at a declared level, and the framework SHALL provide
four:

**literal** — every path. Answers "are these the same configuration".

**authored** — resolved-only fields removed. Answers "did a human write something
different", so a replayed run compares cleanly against the file it came from.

**behavioral** — every field excluded from behavioral sources removed: portable
paths, derived values, template-valued fields. Answers "would these compute
differently", and is the same projection a digest sees.

**by_hash** — differences attributed to the declared hashes they would move.
Answers "which recorded identities change, and why".

A **residual difference** SHALL be defined as one that appears at the literal level
and disappears at the behavioral level. This is the definition run comparison uses,
so the two SHALL share one implementation rather than deriving the notion twice.

#### Scenario: Output location is not a behavioral difference
- **WHEN** two configurations differ only in where they write
- **THEN** the difference appears at the literal level and not at the behavioral one

#### Scenario: Replay compares cleanly against its source
- **WHEN** a run's resolved configuration is compared to the file it was composed
  from at the authored level
- **THEN** the assigned fields do not appear as differences

#### Scenario: Residual difference is named as such
- **WHEN** two configurations differ only in fields no behavioral source reads
- **THEN** the differences are reported as residual

### Requirement: Available levels follow from what the caller supplies
The literal level SHALL require nothing but the operands. The authored and
behavioral levels SHALL require the root type, since both are defined by field
markers. The by_hash level SHALL additionally require the hash registry.

Requesting a level whose inputs were not supplied SHALL be refused naming what is
missing, rather than silently falling back to a weaker comparison — a comparison
that quietly answered a different question than the one asked would be worse than
no comparison.

#### Scenario: Bare mappings
- **WHEN** two mappings are compared with no schema
- **THEN** the literal level is available and the others are refused

#### Scenario: Missing registry
- **WHEN** the by_hash level is requested without a hash registry
- **THEN** it is refused naming the registry, not downgraded to behavioral

### Requirement: Hash attribution reports what it cannot know
At the by_hash level each differing path SHALL be reported in one of three ways: as
moving one or more named hashes; as moving none; or as excluded from every digest
by a field marker.

Independently, the result SHALL name the declared hashes whose sources are built by
a project callable, because a callable's inputs are not statically knowable and an
input-reading one cannot be run at comparison time at all. Such hashes SHALL be
reported as **opaque** — neither implicated nor cleared — rather than omitted.

Reporting only the attributable hashes would imply a completeness the analysis does
not have, and the difference matters: "this change moves no identity" and "this
change moves no identity I can see" support different decisions.

#### Scenario: Difference attributed to a declared hash
- **WHEN** a differing path falls within a field-selected hash's include set
- **THEN** that hash is named as moved

#### Scenario: Difference affects no declared identity
- **WHEN** a differing path is in no include set
- **THEN** it is reported as moving no hash

#### Scenario: Difference is marker-excluded
- **WHEN** a differing path carries a marker excluding it from behavioral sources
- **THEN** it is reported as excluded from every digest

#### Scenario: Callable-built hashes are reported opaque
- **WHEN** a project declares a hash whose source a callable builds
- **THEN** the result names it as opaque rather than treating it as unaffected

### Requirement: Comparison returns data, and one renderer is provided
Comparison SHALL return a structured result a project can render, sort or filter
itself. The framework SHALL also provide a plain-text renderer, because otherwise
every project writes the same one and they drift.

The framework SHALL NOT prescribe a command surface for comparison; a project
declares its own command and decides what its operands mean.

#### Scenario: Project renders its own report
- **WHEN** a project wants a different presentation
- **THEN** it reads the structured result rather than parsing rendered text

#### Scenario: Provided renderer
- **WHEN** a project wants no opinion about presentation
- **THEN** the provided renderer produces a readable report from the same result
