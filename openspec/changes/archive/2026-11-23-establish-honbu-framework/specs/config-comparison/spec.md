## Purpose

Defines schema-aware N-way comparison of configuration operands (`default`, `file`, `composed`, `resolved`) across `literal`, `authored`, `behavioral`, and `by_hash` levels, with exact absence-versus-null distinction, pure hash evaluation, and plain-text rendering.

## ADDED Requirements

### Requirement: Comparison operands are configuration references
Comparison SHALL accept two or more operands referencing `default`, `file`,
`composed` (a prior run's `composed.yaml`), or `resolved` (a prior run's
`resolved.yaml`), each with optional `overrides`, `extra_dirs`, and `config_index`
(defaulting to 1). Both `composed` and `resolved` SHALL accept either a file or a
run directory, normalizing each operand to a mapping before comparison.

#### Scenario: Mixed operand kinds
- **WHEN** an authored file and a prior run's resolved configuration are compared
- **THEN** both normalize and the comparison reports their differences

#### Scenario: One configuration against itself with overrides
- **WHEN** the same source is given twice, the second with overrides
- **THEN** the comparison reports exactly what the overrides changed

#### Scenario: Run directory and invocation config index
- **WHEN** an operand names a run directory (optionally with `config_index=2`)
- **THEN** its persisted `resolved.yaml` or `composed.yaml` is located without
  naming internal bundle paths

#### Scenario: Per-operand search directories
- **WHEN** an operand supplies `extra_dirs`
- **THEN** composition applies those directories when normalizing that operand

#### Scenario: Group fragment
- **WHEN** an operand marks `fragment=True` for a configuration-group file
- **THEN** it is comparable only at `literal`, because it cannot validate against
  a root schema

### Requirement: N-way comparison with exact value distinction
Comparison SHALL support any number of operands, reporting one entry per differing
path carrying each operand's value. Absence (`ABSENT`) SHALL be distinguished from
`None`, empty mappings (`{}`), and empty sequences (`[]`), and agreement SHALL be
omitted. Equality semantics SHALL be consistent across differences and residuals.

#### Scenario: Three configurations with absence vs null
- **WHEN** three configurations hold `None`, omit the key, and hold `2`
- **THEN** all three values `(None, ABSENT, 2)` are reported

#### Scenario: Empty container vs absent
- **WHEN** one operand sets a mapping to `{}` (or a list to `[]`) and another
  omits it
- **THEN** the two are reported as differing rather than collapsing to absent

#### Scenario: Agreement is not reported
- **WHEN** every operand holds the same value at a path
- **THEN** that path does not appear in the result

### Requirement: Comparison levels normalize defaults at schema-backed levels
Comparison SHALL provide four declared levels: `literal` (raw mappings as
written), `authored` (validated through `root_type` to normalize schema defaults,
with resolved-only fields removed), `behavioral` (validated with all
marker-excluded fields removed, including wildcard container paths), and `by_hash`
(validated with pure hashes evaluated and differences attributed). Residual paths
SHALL be paths differing at `literal` that vanish at `behavioral`.

#### Scenario: Authored file compares cleanly against its resolved run
- **WHEN** an authored configuration is compared to its run's `resolved.yaml` at
  `authored` or `behavioral`
- **THEN** schema defaults and resolved-only fields do not appear as spurious
  differences

#### Scenario: Output location is residual, not behavioral
- **WHEN** two configurations differ only in path-marked fields
- **THEN** the difference appears at `literal` (and in `residual_paths`) and not
  at `behavioral`

#### Scenario: Marker-excluded field inside a container with hyphenated keys
- **WHEN** a marked field sits inside a mapping keyed by a hyphenated name
- **THEN** wildcard exclusion matches it cleanly without error

### Requirement: Available levels follow from caller-supplied inputs
`literal` SHALL require only operands; `authored` and `behavioral` SHALL require
`root_type`; `by_hash` SHALL require `root_type` and `hashes`, and SHALL refuse a
missing `command` when the registry contains any command-scoped `FieldHash`.
Requesting an unavailable level SHALL be refused naming what is missing, never
silently downgraded.

#### Scenario: Bare mappings
- **WHEN** two mappings are compared with no `root_type`
- **THEN** `literal` succeeds and `authored`, `behavioral`, and `by_hash` refuse

#### Scenario: Missing registry or command
- **WHEN** `by_hash` is requested without a hash registry, or without `command`
  against a command-scoped `FieldHash`
- **THEN** comparison refuses naming the missing input rather than reporting
  empty hash attribution

### Requirement: Pure hashes are evaluated and input-reading hashes are reported opaque
At `by_hash`, comparison SHALL evaluate every declared pure hash
(`reads_inputs=False` across `FieldHash`, `SourceHash`, and `FannedHash`) against
each validated operand via `hashes.compute(..., input_reading=False)`, reporting
per-hash digest equality, digests, and source differences alongside path
attribution. Only input-reading declarations (`reads_inputs=True`, such as dataset
scans or `ByteHash`) SHALL be reported as `opaque_hashes`.

#### Scenario: Pure SourceHash and FannedHash are evaluated directly
- **WHEN** a registry declares `reads_inputs=False` `SourceHash` and `FannedHash`
  identities (such as `model_config_hash`, `preprocessing_hash`, and per-head
  hashes)
- **THEN** `by_hash` computes their digests and source differences rather than
  marking them opaque

#### Scenario: Input-reading hash reported opaque
- **WHEN** a registry declares a `reads_inputs=True` hash (such as `dataset_hash`
  or a `ByteHash`)
- **THEN** it is named in `opaque_hashes` rather than silently treated as equal

#### Scenario: Behavioral difference moving no declared hash
- **WHEN** a differing behavioral path moves no declared hash
- **THEN** it is still reported in `differences` with empty `moved_hashes`

### Requirement: Comparison returns structured data and a plain-text renderer
Comparison SHALL return a structured `Comparison` result a project can render,
sort, or filter itself, paired with a plain-text `render_comparison` renderer. The
framework SHALL NOT prescribe a CLI command surface for comparison.

#### Scenario: Project renders its own report
- **WHEN** a project wants a Rich table or JSON output
- **THEN** it reads the structured `Comparison` object directly

#### Scenario: Provided renderer
- **WHEN** a project wants a plain-text report
- **THEN** `render_comparison` formats differences, hash summaries, residuals,
  and opaque hashes cleanly
