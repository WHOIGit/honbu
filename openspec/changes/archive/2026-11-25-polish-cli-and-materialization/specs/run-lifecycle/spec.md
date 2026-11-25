## MODIFIED Requirements

### Requirement: Config provenance bundle
A mutating command SHALL atomically persist `composed.yaml`, portable
`resolved.yaml`, and one artifact per computed hash under `config/` before work
begins, persisting deferred hashes as completed and recording each invocation
in `manifest.json`. When opt-in `write_cli_txt=True` is set on `open_run` or
`ProjectSpec` and `argv_text` is non-empty, `open_run` SHALL also atomically
write `config/cli.txt`.

#### Scenario: Deferred hash completed mid-run
- **WHEN** a deferred hash is completed forty minutes into a run
- **THEN** its artifact appears then, and the rest of the bundle was already present

#### Scenario: Completed invocation
- **WHEN** work completes
- **THEN** the manifest records its registered artifacts and identities

#### Scenario: Opt-in cli.txt written for CLI invocations only
- **WHEN** `open_run` runs with `write_cli_txt=True` and non-empty `argv_text`
  versus empty `argv_text`
- **THEN** `config/cli.txt` is atomically written in the first case and omitted
  in the second
