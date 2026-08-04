## ADDED Requirements

### Requirement: Shared canonical hashing
The framework SHALL hash a source object as SHA-256 over one canonical-JSON
encoding — sorted keys, compact separators, floats normalized then JSON-rendered,
non-finite values tokenized, `sha256:` prefix — shared by every consumer so equal
source trees hash byte-identically across projects.

#### Scenario: Equal trees hash identically
- **WHEN** two structurally equal source trees are hashed
- **THEN** they produce identical canonical bytes and the same digest

#### Scenario: Pinned mechanics
- **WHEN** a fixed tricky tree (nested maps, negative zero, non-finite floats,
  unicode) is hashed
- **THEN** it matches a pinned cross-project value

### Requirement: Bare-namespace versioned sources
Hash sources SHALL project in the configuration's own key namespace with no
synthetic wrapper and SHALL carry a `version` field; the configuration-identity
source SHALL carry no version token.

#### Scenario: No wrapper
- **WHEN** a configuration-identity source is produced
- **THEN** its top-level keys are configuration field names

### Requirement: Type-driven field exclusion
Portable-path fields and generated/derived-identity fields SHALL be excluded from
behavioral sources by walking their type markers, never a hand-maintained list.

#### Scenario: Portable path only
- **WHEN** two configurations differ only in path-marked fields
- **THEN** those fields are absent from behavioral sources and the digests are equal

#### Scenario: New derived field
- **WHEN** a field marked as a derived identity is added to the schema
- **THEN** it is excluded from sources without editing the hashing module

### Requirement: Deterministic and fresh identities
The framework SHALL derive deterministic human-readable identities from a hash,
domain-seeded, and SHALL provide fresh per-invocation identities.

#### Scenario: Repeat
- **WHEN** the same hash is labelled twice
- **THEN** the deterministic identity is identical, and a fresh identity differs

### Requirement: Comparison-role registry
Each hash SHALL declare its comparison role — gate or provenance — and, for
gates, the operations it gates, so a generic engine can decide comparability and
ensemblability without domain knowledge.

#### Scenario: Gate mismatch
- **WHEN** two runs differ on a hash that gates an operation
- **THEN** the engine reports that operation as not permitted, naming the hash
