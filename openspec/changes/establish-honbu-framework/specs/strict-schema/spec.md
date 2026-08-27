## ADDED Requirements

### Requirement: Strict model base
The framework SHALL provide a strict model base that forbids unknown fields,
validates assignment, validates defaults and default factories, strips
surrounding whitespace from strings, refuses arbitrary types, and serializes
non-finite floats as floats rather than nulls. Every framework and project
configuration block SHALL inherit it, so a subclass inherits strictness without
restating it.

#### Scenario: Unknown field
- **WHEN** a model receives an unsupported field
- **THEN** validation fails

#### Scenario: Invalid assignment
- **WHEN** a field is assigned a value violating its constraint after construction
- **THEN** the assignment fails

#### Scenario: Invalid default
- **WHEN** a field declares a default violating its own constraint
- **THEN** validation fails rather than passing for callers who do not override it

#### Scenario: Non-finite float reaches the encoder
- **WHEN** a float field holds a non-finite value and the model is dumped
- **THEN** the value survives the dump and the canonical encoder refuses it,
  naming the path, rather than the field silently disappearing

### Requirement: Field markers and walkers
The framework SHALL provide field markers for portable paths, generated/derived
identities, and resolved-only values, plus walkers that enumerate marked fields
across a model tree by dotted path, traversing mapping values and sequence
elements as well as direct submodels.

#### Scenario: Marker walk
- **WHEN** a model tree is walked for a marker
- **THEN** every field carrying that marker is enumerated by dotted path

#### Scenario: Container traversal
- **WHEN** a marked field lives inside a mapping-of-models or sequence-of-models
- **THEN** the walker enumerates it with a container step in its path

### Requirement: Derived identities are declared on the field
A field receiving a computed identity SHALL declare the hash it comes from with a
marker on the field itself, not in a separate table of paths and not by a project
callback. The declared hash name SHALL be validated against the hash registry at
construction. Such a field SHALL receive the digest; any human-readable form SHALL
be a template concern rather than a property of the declaration.

#### Scenario: Marked identity field is assigned
- **WHEN** a configuration is resolved
- **THEN** every field marked as coming from a hash holds that hash's digest

#### Scenario: Unknown hash name
- **WHEN** a field declares a hash the registry does not know
- **THEN** construction fails naming the field and the hash

#### Scenario: Extended block adds an identity field
- **WHEN** a project subclass adds a field marked as coming from a hash
- **THEN** it is assigned without any further declaration

#### Scenario: Only resolved-only fields may be assigned
- **WHEN** a field marked as coming from a hash is not resolved-only
- **THEN** construction fails, because assigning an authored field after its
  digest was computed would make the digest describe a configuration that never ran

### Requirement: Template-valued fields are rendered in place
A field MAY declare that its own value is a template, and resolution SHALL render
it before any output-path template is rendered, so an output path may reference it.
Such a field SHALL be excluded from every digest, because the authored template and
the rendered value would otherwise disagree about what a run was.

#### Scenario: Run identity rendered
- **WHEN** a run-identity field holds a template
- **THEN** it is rendered before the output directory that references it

#### Scenario: The authored template survives
- **WHEN** a template-valued field is rendered
- **THEN** the composed configuration still holds the template it was authored with

### Requirement: Identities are declared, not inferred
A field MAY declare that it is a run identity, and the run manifest SHALL record
every such field's value. The framework SHALL NOT infer identities from hashes: not
every hash warrants an identity, and not every identity derives from a hash. Two
identity fields sharing a leaf name SHALL be refused, so the recorded keys stay
readable and unambiguous.

#### Scenario: Identity not derived from a hash
- **WHEN** a fresh per-invocation identity is declared
- **THEN** the manifest records it alongside identities that do derive from hashes

#### Scenario: A hash without an identity
- **WHEN** a hash is computed and no field declares an identity from it
- **THEN** the manifest records the digest and no identity

#### Scenario: Colliding identity names
- **WHEN** two identity fields in different blocks share a leaf name
- **THEN** construction fails naming both paths

### Requirement: The framework assigns the fields it owns
Resolution SHALL assign only the fields the framework declares as resolved-only:
the configuration digest and the fields fed from named hashes, plus the
template-valued fields it renders in place.

It SHALL NOT assign a seed. A null seed means no seed was pinned, and filling one
would either contradict an already-computed digest or give every unseeded
invocation its own configuration identity.

It SHALL NOT record the active command in the configuration. A configuration is
not per-command: recording one would make the resolved configuration differ
between two commands reading the same authored file, forking the configuration
index and defeating the point of two invocations sharing a bundle. The command is
an invocation fact, recorded per invocation in the manifest and reachable from
templates through the invocation namespace.

#### Scenario: Unpinned seed stays null
- **WHEN** a configuration is resolved without a seed
- **THEN** the seed remains null and the configuration identity is unchanged

#### Scenario: Two commands over one authored configuration
- **WHEN** a producing command and an augmenting command resolve the same authored
  configuration with no overrides
- **THEN** both produce the same resolved configuration and share one
  configuration index

### Requirement: The configuration identity is optional and project-bound
The framework SHALL reserve no hash name and SHALL bind no hash to a field of its
own accord. The runtime block's configuration-digest field SHALL carry no
framework-supplied binding, and the configuration-identity field SHALL default to
null, so that a project declaring no hashes at all is a valid project and the
shipped block carries no identity behavior it cannot satisfy.

A project wanting a hash-derived configuration identity SHALL declare the hash and
bind the field, and MAY bind it to any declared hash — a per-command digest rather
than a whole-configuration one is a legitimate and often better choice. `config_hash`
SHALL be documented as the conventional name without being enforced, and any
operation that ranks or gates on a configuration identity SHALL take the identity's
name as a parameter rather than assuming it.

No shipped default SHALL reference a field the framework does not itself guarantee.

#### Scenario: A project that hashes nothing
- **WHEN** a project declares an empty hash registry and uses the framework runtime
  block unmodified
- **THEN** its commands resolve, run, and record normally, with no configuration
  identity in the manifest

#### Scenario: Project binds its own configuration identity
- **WHEN** a project declares a hash and binds the configuration-digest field to it
- **THEN** the digest is assigned and the identity is recorded, with no further
  declaration

#### Scenario: No default names an ungiven field
- **WHEN** the shipped runtime block is resolved for a command that computes no
  hashes
- **THEN** no template refuses, because no shipped default references the
  configuration digest

### Requirement: Unmarked path guard
The framework SHALL provide a guard enumerating path-typed fields that lack the
portable-path marker, so a new path field cannot silently enter a behavioral
source.

#### Scenario: New unmarked path field
- **WHEN** a path-typed field is added without the marker
- **THEN** the guard reports it

### Requirement: Absence carries no meaning
A null field value SHALL mean null. A block requiring "apply the backend
library's own default" SHALL express it with an explicit deferral value that
survives serialization and appears in behavioral sources, and the framework SHALL
provide the helper that omits deferred knobs when building backend arguments.

#### Scenario: Deferral is visible
- **WHEN** one configuration defers a knob and another pins the same value
- **THEN** their resolved configurations and their digests differ

#### Scenario: Deferred knobs are omitted
- **WHEN** backend arguments are built from a block containing deferred knobs
- **THEN** those knobs are absent from the arguments

### Requirement: The authored configuration is recorded, not inferred
The framework SHALL NOT rely on Pydantic's set-fields tracking to distinguish
authored values from defaults, because resolution assigns fields and revalidates
the tree. The composed configuration persisted by the provenance bundle SHALL be
the record of what was authored.

#### Scenario: Authored values recoverable
- **WHEN** a resolved configuration is inspected for what the user authored
- **THEN** the composed configuration answers it

### Requirement: Project-assembled root from framework block types
The framework SHALL export strict base block types and machinery; a project SHALL
assemble its own flat root configuration from those block types plus its domain
block types, keeping all blocks at the top level.

#### Scenario: Flat assembly
- **WHEN** a project assembles a root from framework and domain block types
- **THEN** every block is a top-level field with no wrapper namespace

### Requirement: Output block and output leaf base types
The framework SHALL export an output-block base type carrying a literal directory
and a directory template, and an output-leaf base type carrying an enabled flag,
a literal directory and a directory template. A project SHALL subclass either to
add domain fields or to change a default.

#### Scenario: Leaf template
- **WHEN** an output leaf declares a directory template referencing a hash
- **THEN** two invocations with differing values for that hash write to different
  leaves rather than colliding

#### Scenario: Enabled leaf without a directory
- **WHEN** an output leaf is enabled and its directory is null after resolution
- **THEN** resolution refuses, naming the leaf

### Requirement: Block-level extension
The framework SHALL let a project substitute a subclass of a base block. Such a
subclass MAY add fields, override defaults, retype fields and change constraints,
and the walkers SHALL honor added marked fields.

#### Scenario: Extended base block
- **WHEN** a project subclasses a base block to add a marked field
- **THEN** the walkers enumerate the added field and the base fields are unchanged

#### Scenario: Overridden default
- **WHEN** a project subclasses an output block solely to change a default
- **THEN** the subclass is accepted

### Requirement: Root-contract guard
The framework SHALL provide a guard that verifies a project root contains the
required base blocks — an experiment block, a runtime block, an output root, and
at least one output block — and that each is the framework base type or a
subclass. The guard SHALL NOT inspect fields beyond that; what a subclass does to
inherited fields is the project's responsibility, documented rather than enforced.

#### Scenario: Missing base block
- **WHEN** a root omits a required base block
- **THEN** the guard fails at validation time

#### Scenario: Subclass alters an inherited field
- **WHEN** a project subclass removes, retypes or re-constrains an inherited field
- **THEN** the guard accepts it
