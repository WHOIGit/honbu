## 1. File-Root Search-Path Inheritance & Optional `cli.txt`

- [x] 1.1 Update `compose_file` (`src/honbu/flow/compose.py`) and
  `load_config("file", ...)` (`src/honbu/flow/load.py`) so authored file roots
  search `[target.parent, *extra_dirs, local, packaged]` when `compose_spec` or
  `project` is supplied, keeping `resolved` and `fork` self-contained
- [x] 1.2 Add `write_cli_txt: bool = False` to `ProjectSpec`
  (`src/honbu/flow/project.py`) and `open_run` (`src/honbu/flow/run.py`),
  atomically writing `config_dir / "cli.txt"` when enabled and `argv_text` is
  non-empty
- [x] 1.3 Verify: `pytest tests/test_compose.py tests/test_flow.py tests/test_run.py`
  covering file-root packaged group fallback and `cli.txt` creation/omission

## 2. Materialization `from_file` Transitive Shadow & `--clobber`

- [x] 2.1 Update `closure_for_root` and `plan_materialization` in
  `src/honbu/materialize/trees.py` so `MaterializationRoot.from_file` searches
  `[target.parent, local, packaged]` and skips local-only choices absent from
  `pkg_root` instead of raising `DeclarationError`
- [x] 2.2 Update `build_materialize_command` in `src/honbu/cli/presets.py` to
  use `--clobber` (replacing `--overwrite`) and accept optional `--from <file>`
- [x] 2.3 Verify: `pytest tests/test_materialize.py tests/test_cli.py`
  covering transitive local edits pulling unmaterialized packaged files,
  custom local-only groups skipped cleanly, and `--clobber` / `--from`

## 3. Forgiving Positional `[CONFIG]` Resolver & `-c` Preset

- [x] 3.1 Implement `resolve_positional_config` in `src/honbu/cli/helpers.py`
  and export it from `honbu.cli` (two-channel deduplicated lookup, 0/1/>1
  refusals, run-dir guardrail, zero Typer import)
- [x] 3.2 Update `config_option` / `ConfigOpt` in `src/honbu/cli/presets.py`
  to expose `("-c", "--config")`
- [x] 3.3 Verify: `pytest tests/test_cli.py tests/test_layering.py`

## 4. `@packaged` / `@packaged:<name>` Compare Operands & N-Way Preset

- [x] 4.1 Extend `OperandSource` in `src/honbu/compare/configs.py` with
  `"packaged"` and add optional `config_name: str | None = None` on
  `ConfigOperand`; wire `"file"` operands to inherit `compose_spec` search path
- [x] 4.2 Update `build_compare_command` in `src/honbu/cli/presets.py` to
  accept N-way operands (`@default`, `@packaged`, `@packaged:<name>`, run dirs,
  and forgiving config names/paths via `resolve_positional_config`) and
  `--config-dir`
- [x] 4.3 Verify: `pytest tests/test_compare.py tests/test_cli.py tests/test_example_project.py`

## 5. Full Verification

- [x] 5.1 Verify: `openspec validate polish-cli-and-materialization --strict`,
  `openspec validate --all`, and full `pytest` suite pass cleanly
