## MODIFIED Requirements

### Requirement: Comparison operands are configuration references
Comparison SHALL accept two or more operands referencing `default`, `packaged`
(packaged defaults ignoring local shadows, with optional `config_name` for
named preset roots), `file` (inheriting `compose_spec` fallbacks), `composed`,
or `resolved`, each with optional `overrides`, `extra_dirs`, and `config_index`
(defaulting to 1). `composed` and `resolved` SHALL accept a file or run dir.

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

#### Scenario: Packaged operand ignores local shadow and supports named presets
- **WHEN** a `ConfigOperand(source="packaged")` (or with `config_name="configA"`)
  is compared against `default` or a local file
- **THEN** it composes strictly from packaged defaults ignoring `./configs/`
