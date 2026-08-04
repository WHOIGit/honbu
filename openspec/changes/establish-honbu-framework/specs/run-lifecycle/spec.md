## ADDED Requirements

### Requirement: Command-aware side-effect-free resolution
Resolution SHALL be command-aware and SHALL perform no filesystem writes; it
SHALL derive identities and resolve output paths for the active command, and MAY
resolve without external inputs for read-only inspection, reporting
input-derived identities as deferred when their inputs are absent.

#### Scenario: Read-only inspection without inputs
- **WHEN** a configuration is resolved for inspection with its inputs absent
- **THEN** configuration identity and active output paths resolve and
  input-derived identities are reported as deferred

### Requirement: Output-path templating and resolution
Output paths SHALL support dotted resolved tokens, timestamps, and deterministic
and fresh identity tokens, resolved with absolute, working-directory-relative,
and parent-relative semantics.

#### Scenario: Templated run directory
- **WHEN** a run template references experiment, timestamp, and run identity
- **THEN** it renders to an absolute directory under the output root

### Requirement: Collision safety
A mutating command SHALL refuse a non-empty output target by default and SHALL
clear only a validated concrete target under an explicit override, never a
template, root, working directory, or home.

#### Scenario: Existing output
- **WHEN** a non-empty output exists without the override
- **THEN** the command fails before modifying it

### Requirement: Config provenance bundle and run manifest
Before work, a mutating command SHALL atomically persist the composed
configuration, the authoritative resolved configuration and its rendering, the
invocation text, and the overrides; and SHALL track an atomic manifest recording
command, state, timestamps, identities, primary artifact paths, and failures.

#### Scenario: Failed invocation
- **WHEN** work raises after initialization
- **THEN** the manifest becomes failed and the provenance bundle remains

#### Scenario: Completed invocation
- **WHEN** work completes
- **THEN** the manifest records its primary artifact paths and identities
