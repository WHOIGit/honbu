## ADDED Requirements

### Requirement: Strict model base and markers
The framework SHALL provide a strict model base that forbids unknown fields and
field markers for portable paths, generated/derived identities, and
resolved-only values, plus walkers that enumerate marked fields across a model
tree.

#### Scenario: Unknown field
- **WHEN** a model receives an unsupported field
- **THEN** validation fails

#### Scenario: Marker walk
- **WHEN** a model tree is walked for a marker
- **THEN** every field carrying that marker is enumerated by dotted path

### Requirement: Project-assembled root from framework block types
The framework SHALL export strict base block types and machinery; a project SHALL
assemble its own flat root configuration from those block types plus its domain
block types, keeping all blocks at the top level.

#### Scenario: Flat assembly
- **WHEN** a project assembles a root from framework and domain block types
- **THEN** every block is a top-level field with no wrapper namespace

### Requirement: Block-level extension
The framework SHALL let a project substitute a subclass of a base block to add
domain fields; such a subclass SHALL only add fields, and the walkers SHALL honor
added marked fields.

#### Scenario: Extended base block
- **WHEN** a project subclasses a base block to add a marked field
- **THEN** the walkers enumerate the added field and the base fields are unchanged

### Requirement: Root-contract guard
The framework SHALL provide a guard that verifies a project root contains the
required base blocks, that each is the framework base type or a subclass, and
that subclass blocks do not remove or retype base fields.

#### Scenario: Missing base block
- **WHEN** a root omits a required base block
- **THEN** the guard fails at validation time
