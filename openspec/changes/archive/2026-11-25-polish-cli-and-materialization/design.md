## Context

`honbu` shipped `establish-honbu-framework` cleanly (`bb804dd`). During the
detailed CLI and configuration spec reviews for `image-classifier-dojo` and
`sati`, five small cross-cutting enhancements surfaced that belong in `honbu`
rather than being duplicated in both downstream projects.

## Goals / Non-Goals

**Goals**
- Make standalone root files (`-c <path>` and positional `[CONFIG]`) resolve
  unmaterialized packaged groups seamlessly.
- Make `init --from <local_file>` pull only newly-referenced unmaterialized
  packaged YAMLs even across transitive local edits and custom local-only groups.
- Provide one shared forgiving positional `[CONFIG]` resolver (`honbu.cli`)
  plus `-c`/`--config` and consistent `--clobber` across Typer presets.
- Support `@default`, `@packaged`, and `@packaged:<name>` cleanly in
  `honbu.compare` and `build_compare_command`.
- Allow projects to opt into atomic `config/cli.txt` writes without breaking
  Honbu's single-writer rule for `config/`.

**Non-Goals**
- Changing Honbu's layering boundaries (`honbu.core` stays Hydra/YAML/Typer-free;
  `honbu.flow` and `honbu.cli` stay Typer-free).
- Changing manifest schema or hash mechanics.

## Decisions

### 1. File-Root Search-Path Inheritance
In `honbu.flow.load.load_config("file", ...)`: when `spec` is available,
compute fallback search dirs via `build_search_path(spec, extra_dirs=extra_dirs, cwd=cwd)`
and pass them behind `target.parent` so `[target.parent, *extra_dirs, local, packaged]`
resolves cleanly. `compose_file` also accepts optional `compose_spec` (or
`fallback_dirs`) so direct callers and `normalize_operand("file", ...)` share
the exact same behavior. `resolved` and `fork` remain self-contained snapshots
with no packaged fallback.

### 2. `MaterializationRoot.from_file` Transitive Local Shadow & Local-Only Skip
In `honbu.materialize.trees`:
- `closure_for_root`: for `root.file_path`, keep `compose_spec.local` active
  alongside `target.parent` in `extra_dirs` so `[target.parent, ./configs, packaged]`
  sees transitive edits inside `./configs/` (e.g., an edited group file pulling
  a new packaged YAML).
- `plan_materialization`: for `MaterializationRoot.from_file`, if a relative
  choice path does not exist under `pkg_root` (a custom local-only YAML the
  user authored), skip it cleanly instead of raising `DeclarationError`.
  Packaged `MaterializationRoot(config_name=...)` roots continue raising
  `DeclarationError` on missing packaged files.
- `build_materialize_command`: rename `--overwrite` → `--clobber` (matching
  `CONVENTIONAL_FLAGS["clobber"]`) and add optional `--from <file>` to plan a
  `MaterializationRoot.from_file` closure directly.

### 3. Shared Forgiving Positional `[CONFIG]` Resolver (`resolve_positional_config`)
Expose `resolve_positional_config(token, *, compose_spec=..., project=..., extra_dirs=(), cwd=None, reject_run_dir=True) -> Path`
in `honbu.cli` (library-agnostic, zero Typer dependency):
- Refuses run directories (`manifest.json` or `config/resolved.yaml` present)
  when `reject_run_dir=True`, hinting `--resolved` or `--fork-from`.
- Channel 1 (direct filesystem): checks `<tok>` (appending `.yaml` when
  omitted; `.yml` is refused cleanly because Hydra composes `.yaml` only).
- Channel 2 (search path): skipped for absolute, `./`-anchored, or
  `..`-traversing paths; otherwise walks `build_search_path(spec,
  extra_dirs=extra_dirs, cwd=cwd)` and takes the **first** (highest-precedence)
  directory containing `<tok>` or `<tok>.yaml` (so local `./configs/` shadowing
  packaged defaults counts as one shadowed match, never ambiguity).
- Deduplicates candidates by `Path.resolve()`:
  - `0` matches → `RefusalError` hinting `key=value` override or `--flag`.
  - `1` match → returns that resolved `Path`.
  - `>1` matches → `RefusalError` listing all candidate paths and prompting
    `-c` / `--config`.
- Update `honbu.cli.presets.config_option` / `ConfigOpt` default declarations
  to `("-c", "--config")`.

### 4. `@packaged` / `@packaged:<name>` Compare Operands
- Extend `OperandSource` to `Literal["default", "packaged", "file", "composed", "resolved"]`
  and add optional `config_name: str | None = None` on `ConfigOperand`.
- In `normalize_operand`: `"packaged"` composes with
  `ComposeSpec(packaged=spec.packaged, local=None, config_name=operand.config_name or spec.config_name)`,
  ignoring `./configs/` completely.
- Update `honbu.cli.presets.build_compare_command`:
  - Accept N-way positional operands (`LEFT RIGHT [MORE...]`, via
    `split_dash_rule` / `list[str]`) plus optional `--config-dir`.
  - Recognize `@default`, `@packaged`, `@packaged:<name>`, run directories, and
    forgiving config names/paths via `resolve_positional_config` (replacing bare
    `"default"` so a file named `default.yaml` never collides).

### 5. Optional Atomic `config/cli.txt`
Add `write_cli_txt: bool = False` to `ProjectSpec` and `open_run(..., write_cli_txt: bool | None = None)`
(resolving `None` to `project.write_cli_txt` if `project` is given, else `False`).
When enabled and `argv_text.strip()` is non-empty, `open_run` atomically writes
`config_dir / "cli.txt"` (using `atomic_write_text`) alongside `composed.yaml`
and `resolved.yaml` so Honbu remains the sole writer for `config/`.
