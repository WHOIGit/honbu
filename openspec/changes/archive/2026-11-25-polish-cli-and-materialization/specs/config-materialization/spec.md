## MODIFIED Requirements

### Requirement: Materialization is configs-only and copies the closure of declared roots
Materialization SHALL copy packaged configuration files only from declared
roots (packaged config names with optional overrides, or an external/local file
via `MaterializationRoot.from_file`) using `hydra.runtime.choices` (excluding
`hydra/*` and stripping `@package`). `from_file` SHALL search
`[target.parent, local, packaged]` so transitive local edits are honored and
SHALL skip local-only choices absent from packaged defaults.

#### Scenario: Root and its transitive dependencies
- **WHEN** a packaged root is materialized
- **THEN** the root and every packaged group option it transitively selects are
  copied

#### Scenario: Package-relocated group options resolve cleanly
- **WHEN** a defaults list uses `@package` syntax (such as
  `/backbone/torchvision@model.image_input.backbone: efficientnet_b0`)
- **THEN** the `@package` suffix is stripped and `backbone/torchvision/efficientnet_b0.yaml`
  is materialized

#### Scenario: Framework-internal groups are excluded
- **WHEN** a closure is computed
- **THEN** `hydra/*` internal groups are excluded from the copy plan

#### Scenario: An authored file as a root
- **WHEN** an external authored YAML file outside the packaged tree is given as
  a root
- **THEN** only the packaged options it transitively references are copied into
  the local tree, and the authored file itself is left where it is

#### Scenario: Edited local config pulls newly referenced packaged group and skips local-only files
- **WHEN** a local file under `./configs` (or a child config it pulls in)
  references both a custom local-only group and an unmaterialized packaged group,
  and `plan_materialization` runs on `MaterializationRoot.from_file`
- **THEN** already-materialized files are marked `skip`, the local-only group
  is silently skipped without error, and the unmaterialized packaged file is
  marked `create`
