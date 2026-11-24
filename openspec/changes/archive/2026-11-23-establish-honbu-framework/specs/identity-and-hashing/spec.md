## Purpose

Defines Honbu's content-addressed identity foundation: frozen canonical JSON encoding, marker-driven behavioral field exclusion, the four hash declaration forms (`FieldHash`, `SourceHash`, `ByteHash`, `FannedHash`), declared deferral, per-hash artifacts, and thread-safe deterministic and fresh human-readable identities.

## ADDED Requirements

### Requirement: Frozen canonical encoding
The framework SHALL hash a source object as SHA-256 over one canonical JSON
encoding — sorted keys, compact separators, UTF-8 bytes with no Unicode
normalization, floats normalized to twelve significant digits with negative zero
folded to positive zero, `sha256:` prefix — shared by every consumer so equal
source trees hash byte-identically across projects. The encoding SHALL accept no
options and SHALL expose bytes as its primitive.

#### Scenario: Equal trees hash identically
- **WHEN** two structurally equal source trees are hashed
- **THEN** they produce identical canonical bytes and the same digest

#### Scenario: Authored order is irrelevant
- **WHEN** two sources contain the same keys in different insertion order
- **THEN** their digests are equal

#### Scenario: Negative zero folds
- **WHEN** a source holds `-0.0` and an otherwise identical source holds `0.0`
- **THEN** their digests are equal

#### Scenario: Pinned mechanics
- **WHEN** a fixed tricky tree (nested maps, negative zero, unicode, nested
  sequences, twelve-significant-digit floats) is hashed
- **THEN** it matches a pinned cross-project value

### Requirement: The encoder refuses what it cannot encode unambiguously
The encoder SHALL refuse non-string mapping keys, non-finite floats, and any
value outside null, string, integer, boolean, float, mapping, and sequence,
rather than coercing or tokenizing it; each refusal SHALL name the dotted path of
the offending value.

#### Scenario: Non-string key
- **WHEN** a source contains an integer mapping key
- **THEN** encoding fails rather than stringifying the key

#### Scenario: Non-finite float
- **WHEN** a source contains NaN or an infinity
- **THEN** encoding fails naming the path

#### Scenario: Unencodable value
- **WHEN** a source contains a value of an unsupported type
- **THEN** the error names the type and the dotted path

### Requirement: Encoder version recorded outside every digest
The canonical encoder SHALL carry a frozen version that is recorded in each hash
artifact and in the run manifest, and that SHALL NOT appear in any hash source,
any digest, or the resolved configuration. A change to the encoding SHALL be a
major framework version that bumps every hash recipe version.

#### Scenario: Diagnosable mismatch
- **WHEN** two runs recorded under different encoder versions are compared
- **THEN** the recorded encoder versions explain the digest difference

### Requirement: Bare-namespace versioned sources
Hash sources SHALL project in the configuration's own key namespace with no
synthetic wrapper and domain sources SHALL carry a `version` field by default.
The configuration-identity source MAY omit a version token (or carry one if a
project opts in), and `SourceHash` and `FannedHash` SHALL accept
`versioned: bool = True` so a versionless custom source opts out cleanly.

#### Scenario: No wrapper
- **WHEN** a configuration-identity source is produced
- **THEN** its top-level keys are configuration field names

#### Scenario: Versionless custom source opts out explicitly
- **WHEN** a `SourceHash` is declared with `versioned=False` and returns a source
  without a `version` key
- **THEN** hashing succeeds with `entry.version = None`, whereas `versioned=True`
  refuses a source omitting `version`

### Requirement: Marker-aware projection helpers
The core package SHALL export `project_fields` and `project_model` so custom
`SourceHash` and `FannedHash` builders can project a root configuration or
submodel while automatically applying all marker-driven exclusions rather than
maintaining hand-written exclusion lists.

#### Scenario: Submodel projection excludes marked fields
- **WHEN** a custom `SourceHash` calls `project_model` on a configuration submodel
  containing portable-path or derived fields
- **THEN** those marked fields are pruned from the returned mapping automatically

### Requirement: Type-driven field exclusion
Marked fields SHALL be excluded from behavioral sources by walking their type
markers — including through mapping values and sequence elements — never a
hand-maintained list. The marked set is portable paths, generated/derived
identities, templates, and directives. Marker exclusion SHALL be applied after any
declared selection and SHALL NOT be overridable by a declaration.

A **directive** is a field authored by hand that the work does not read: an
instruction to the framework about how runs are generated rather than an input to
what a run computes. It is a distinct marker from the derived one, which asserts
that the framework *generated* the value — a claim that would be false here, and
the field does stay in the persisted configuration where it is useful provenance.

#### Scenario: Directive field
- **WHEN** two configurations differ only in a directive-marked field
- **THEN** their behavioral digests are equal

#### Scenario: Portable path only
- **WHEN** two configurations differ only in path-marked fields
- **THEN** those fields are absent from behavioral sources and the digests are equal

#### Scenario: New derived field
- **WHEN** a field marked as a derived identity is added to the schema
- **THEN** it is excluded from sources without editing the hashing module

#### Scenario: Marked field inside a container
- **WHEN** a marked field lives inside a mapping-of-models or sequence-of-models
- **THEN** it is excluded from every element rather than silently retained

### Requirement: Declared hashes
A project SHALL declare each hash in one of four forms: selected configuration
fields (`FieldHash`), a project-built source (`SourceHash`, versioned by default
via `versioned=True`), a digest over file bytes (`ByteHash`), or a fanned-out
declaration (`FannedHash`). Declared field paths SHALL be validated against the
schema when the registry is constructed.

#### Scenario: Unknown declared path
- **WHEN** a declaration references a configuration path that does not exist
- **THEN** registry construction fails naming the path

### Requirement: Deferral is declared, not discovered
A declaration SHALL state whether computing it reads the filesystem or the
network (`reads_inputs=True`). Every such hash SHALL defer past resolution
regardless of input presence, SHALL NOT bind a `FromHash` configuration field,
and SHALL be completed during work via `run.complete_hash()` and read via
`run.hash()` or `run.hash_entry()`.

#### Scenario: Inputs present but the hash still defers
- **WHEN** an input-reading hash is resolved on a machine where its inputs exist
- **THEN** it is still reported deferred, so resolution neither stalls on a long
  scan nor varies by machine

#### Scenario: Read-only inspection reports the same set
- **WHEN** a configuration is inspected on two machines with different data present
- **THEN** both report the same resolved identities and the same deferred set

#### Scenario: Deferred hash cannot bind a FromHash field
- **WHEN** a schema field declares `FromHash` naming a `reads_inputs=True` hash
- **THEN** validation fails at construction naming the field and the deferred hash

#### Scenario: Completion during work and reading across resume
- **WHEN** a deferred hash is completed with its inputs during work
- **THEN** its artifact is written, the manifest records its value, and
  `run.hash(name)` returns that digest on both the current run and a resumed run

### Requirement: Fanned-out hash declarations
A declaration that fans out SHALL yield one entry per key of a mapping or per
element of a
sequence. Each entry SHALL persist as its own artifact named for the hash and its
key or index. Keys SHALL be sanitized as path components, and two keys that
sanitize to the same filename SHALL be refused rather than overwriting.

#### Scenario: Per-key artifacts
- **WHEN** a declaration fans out over a mapping of model heads
- **THEN** one artifact per head is written, named for the hash and the head

#### Scenario: Colliding keys
- **WHEN** two keys sanitize to the same filename
- **THEN** the write is refused naming both keys

#### Scenario: Sequence without natural keys
- **WHEN** a declaration fans out over a sequence
- **THEN** entries are keyed by index

#### Scenario: A fanned hash cannot feed an identity field
- **WHEN** a configuration field declares it receives a fanned hash
- **THEN** construction fails, because one field cannot hold many digests

#### Scenario: Each entry records its own key
- **WHEN** a fanned entry is read back
- **THEN** it names both its hash and its key, rather than requiring its filename
  to be parsed

### Requirement: Per-hash artifacts
Each computed hash SHALL be persisted as its own artifact recording the hash
name, value, recipe version, an uninterpreted provenance label, the encoder
version, and the source object it digests. An absent source SHALL mean the digest
was taken over bytes. Verification SHALL report verified, mismatched, or
unverifiable.

#### Scenario: Independent re-verification
- **WHEN** a persisted hash artifact carrying a source is verified
- **THEN** re-encoding that source reproduces the recorded value

#### Scenario: Byte digest cannot be verified from itself
- **WHEN** a persisted hash artifact carries no source
- **THEN** verification reports it unverifiable rather than verified or mismatched

### Requirement: Deterministic and fresh identities
The framework SHALL derive deterministic human-readable identities from a hash,
domain-seeded, and SHALL provide fresh per-invocation identities. Deterministic
identity generation SHALL be thread-safe, guarding any shared generator RNG state.

#### Scenario: Repeat
- **WHEN** the same hash is labelled twice
- **THEN** the deterministic identity is identical, and a fresh identity differs

#### Scenario: Concurrent deterministic labelling
- **WHEN** multiple threads call `deterministic_id` concurrently
- **THEN** every call returns the exact deterministic identity for its inputs
  without cross-thread RNG corruption

### Requirement: Unknown hashes are ignored
A consumer reading a run's hash artifacts SHALL ignore hash names it does not
recognize. Hash artifacts SHALL NOT record which operations a hash gates; that
authority belongs to the operation being requested.

#### Scenario: Historical hash
- **WHEN** a run carries a hash name the current registry does not declare
- **THEN** it is ignored rather than failing the read
