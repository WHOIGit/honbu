## Why

Reviewing the upcoming `honbu` adoption plans for both `image-classifier-dojo`
and `sati` surfaced six small, shared framework polish items that eliminate
duplicated glue across both projects and fix two real edge cases in file-root
composition and `from_file` materialization:

1. `load_config("file", ...)` currently calls `compose_file(path)` with
   `packaged=None` and `local=target.parent`, so standalone authored roots
   (`-c configs/configA.yaml`, `-c studies/v1_baseline.yaml`) fail when
   referencing unmaterialized packaged groups.
2. `MaterializationRoot.from_file(...)` currently composes with `local=None`
   and errors on local-only choices absent from packaged defaults, preventing
   users from editing a local config (or child group) and running `init --from`
   to pull in only newly-referenced unmaterialized packaged YAMLs.
3. Both projects want identical forgiving positional `[CONFIG]` lookup (two-channel
   deduplicated across direct path and search path) paired with exact
   `-c` / `--config`, and consistent `--clobber` spelling on `init` /
   `build_materialize_command`.
4. Both projects want Option C `inspect config-compare` supporting N-way
   operands and unambiguous `@default`, `@packaged`, and `@packaged:<name>`
   designators instead of bare `"default"`.
5. Both projects want opt-in `config/cli.txt` persistence when invoked via CLI,
   preserving Honbu's single-writer rule for `config/`.

## What Changes

- **`honbu.flow.load` / `honbu.flow.compose`**: When `load_config("file", ...)`
  receives a `compose_spec` or `project`, search `[target.parent, *extra_dirs,
  local, packaged]` so standalone file roots resolve unmaterialized packaged
  groups seamlessly (while `resolved` and `fork` snapshots stay self-contained).
- **`honbu.materialize`**:
  - In `closure_for_root` / `plan_materialization` for
    `MaterializationRoot.from_file`, search `[target.parent, local, packaged]`
    so transitive local edits under `./configs` are respected, and silently
    skip local-only closure entries absent from packaged defaults.
  - Add `--from` support and standardize `--overwrite` → `--clobber` on
    `honbu.cli.presets.build_materialize_command` (matching `CONVENTIONAL_FLAGS`).
- **`honbu.cli` & `honbu.cli.presets`**:
  - Add a library-agnostic forgiving positional `[CONFIG]` resolver helper
    (`resolve_positional_config`) implementing two-channel deduplicated lookup
    (direct filesystem `<tok>` / `<tok>.yaml` vs. highest-precedence
    search-path match), 0/1/>1 refusal rules, and run-directory guardrails.
  - Add `-c` short option to `config_option` / `ConfigOpt` (`"-c", "--config"`).
- **`honbu.compare` & `build_compare_command`**:
  - Add `"packaged"` to `OperandSource` (with optional `config_name` on
    `ConfigOperand` for `@packaged:<name>`), composing strictly from packaged
    defaults (`local=None`).
  - Update `build_compare_command` from bare `"default"` to N-way operands
    accepting `@default`, `@packaged`, `@packaged:<name>`, run directories, and
    forgiving config names/paths.
- **`honbu.flow.project` & `honbu.flow.run`**:
  - Add opt-in `write_cli_txt: bool = False` on `ProjectSpec` and `open_run`
    (defaulting to `project.write_cli_txt` when omitted); when `True` and
    `argv_text` is non-empty, atomically write `config/cli.txt` inside the run's
    config directory.

## Capabilities

### New Capabilities

- *(none — extends existing Honbu capabilities)*

### Modified Capabilities

- `config-and-cli`: `load_config("file", ...)` search-path fallback,
  `resolve_positional_config` in `honbu.cli`, `-c`/`--config` and updated
  `build_materialize_command` / `build_compare_command` in `honbu.cli.presets`,
  and `ProjectSpec.write_cli_txt`.
- `config-materialization`: `MaterializationRoot.from_file` transitive local
  shadow search and graceful skipping of local-only choices.
- `config-comparison`: `"packaged"` `OperandSource` (and optional `config_name`
  for `@packaged:<name>`).
- `run-lifecycle`: opt-in atomic `config/cli.txt` persistence via `open_run` /
  `ProjectSpec`.

## Impact

- `src/honbu/flow/{compose,load,project,run}.py`
- `src/honbu/materialize/trees.py`
- `src/honbu/compare/configs.py`
- `src/honbu/cli/{helpers,presets,__init__}.py`
- `tests/` — focused unit and integration tests for all five enhancements;
  `test_layering` remains green.
