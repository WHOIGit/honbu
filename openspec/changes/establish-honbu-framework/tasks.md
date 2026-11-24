## 1. Core (the non-negotiable foundation)

- [x] 1.1 `errors`: `HonbuError` / `RefusalError` base, so a user-facing refusal
  exits cleanly without a traceback.
- [x] 1.2 `canonical`: frozen canonical encoding (bytes primitive), refusals with
  dotted paths, `hash_source`, frozen encoder version, and a pinned tricky-tree
  guard test.
- [x] 1.3 `model`: strict base (forbid extras, validate assignment and defaults,
  strip whitespace, non-finite survive serialization), the deferral sentinel and
  `backend_kwargs`.
- [x] 1.4 `markers`: markers and composed aliases, container-aware marker walkers
  with memoization, wildcard-expanding pruner, unmarked-path guard, and the
  addressable-field-path check — schema plus composed-data in one traversal, with
  container steps traversable on request and sequence entries addressed by index
  (D24).
- [x] 1.5 `paths`: union sanitization for every platform, sanitization warnings,
  absolute-reference detection in both syntaxes, platform path-length budget, and
  the cwd-relative rule — a leading `./` or `../` means the working directory, a bare
  relative path means the root the declaration names. It began as a private helper
  beside the resolver, which was the wrong home: it is a fact about the path grammar
  that everything anchoring a configured path answers to, and the pilot needed it to
  place a sweep directory by the same rule rather than inventing a second one.
- [x] 1.6 `io`: atomic write (unique temp + rename, residue-free), text and
  artifact-JSON wrappers kept separate from the canonical encoder.
- [x] 1.7 `templates`: pure renderer, token grammar including the invocation
  namespace, extensible function registry with `emits_path`, sanitization
  exemption for path-marked sources, and the token-path extractor the command
  registry reads.
- [x] 1.8 `hashes`: the four declaration forms, declared-path validation at
  registry construction, `HashEntry`, per-hash artifact writer/reader, tri-state
  verification, deferred identities.
- [x] 1.9 `hashes`: `FannedHash` — one entry per mapping key or sequence index,
  each artifact named `<name>.<key>.json` with the key sanitized, colliding
  filenames refused, the key recorded in the artifact, and a fanned name refused
  on an identity field.
- [x] 1.10 `hashes`: declared deferral — an input-reading declaration always
  defers, so the deferred set is static and machine-independent.
- [x] 1.11 `targets`: containment-based validation, occupant-describing refusal,
  scope-confined clearing for augmenting commands, case-collision warning.
- [x] 1.12 Deterministic domain-seeded human IDs + fresh IDs.

## 2. Schema composition

- [x] 2.1 Export base block types: experiment, runtime carrying the run
  identities, output block and output leaf.
- [x] 2.2 `validate_root_contract` — the four requirable blocks present and of
  the base type or a subclass, and nothing beyond that.

## 3. L2 — Flow

- [x] 3.1 `commands`: `TargetSpec`, `StepSpec`, `CommandSpec`, and a registry
  validating declared paths, hash names, shape, and the deferred-token rule at
  construction.
- [x] 3.2 `manifest`: the live single-writer run record — six states derived
  from an append-only timeline, invocations with config indices, artifacts,
  progress, best, lineage, auto-flushing atomically.
- [x] 3.3 `compose`: exported search-path assembly with defined precedence, the
  Hydra call at module scope, four mutually exclusive configuration sources
  declared per command, the dash-rule argv split with declared option arity and
  positionals, the bare-top-level-comma refusal pointing at the sweep block, and
  compositor errors translated to refusals rather than escaping as tracebacks.
- [x] 3.4 `FromHash` field marker: assigns the digest, validated against the hash
  registry and refused on a field that is not resolved-only.
- [x] 3.5 Resolver walking the fixed phase order, returning a pure `Resolution`
  with both configuration views, the hash entries, the deferred set, the resolved
  target and the warnings.
- [x] 3.6 `Run` and `open_run`: the live half, owning the manifest and every
  write — `step()`, `complete_hash()`, `record_artifact()`, the provenance bundle
  in three waves, configuration-index dedup, resume, recorded lineage.
- [x] 3.7 `harness`: `dispatch_command` threading parse→compose→resolve→run with
  no CLI-library dependency, plus Honbu's own `Application` dispatcher with
  longest-path matching, declaration-derived help, source loading for all four
  configuration sources, and refusal-versus-defect exit codes.

## 4. Operations

- [x] 4.0 Flatten the install and reverse the reserved hash name (D20, D21):
  compositor, config library and YAML into core dependencies; delete the three
  "install the extra" refusal branches; add the layering guard test asserting no
  core module imports them; drop the `FromHash` binding from the base runtime
  block and default the configuration-identity field to null; add
  `submitit`/`optuna` extras.
- [x] 4.1 Sweep block and expansion (D22, D24): `gate` / `axes` / `covariant_axes`
  as an open mapping in the root schema, flat-and-nested key normalization recorded
  per child, one dimension per axis whatever its kind, the bare-top-level-comma
  refusal pointing at the block, and the sweep-definition digest under the standard
  exclusions. The open mapping needs one accommodation the spec did not anticipate:
  the compositor composes in struct mode, so a bare `sweep.axes.<path>=` is rewritten
  to its force-add form, since every key under the block is open by construction and
  the prefix would otherwise depend on whether a previous sweep used that key.
- [x] 4.1a The child-distinguishing gate: declared identifier, stability
  verification by resolving under two invocation contexts differing in both clock
  and entropy, the must-appear-in-the-output-template rule, and a refusal naming
  colliding children and the axes that failed to separate them — which are exactly
  the axes whose realized values differ across the collision. Plus a check the spec
  had folded into the template rule and which does not follow from it: the resolved
  child directories must themselves be distinct, since a lossy template function can
  collapse two sound gate values into one directory.
- [x] 4.1b Preparation and the sweep directory: materialize every child in the
  initialized state running nothing, strip the sweep block from each child so its
  digests match a hand-run configuration, idempotent re-preparation, the sweep
  `config/` holding the immutable index, and a producing command's refusal of an
  active sweep block — which lives in flow rather than here, as a structural dict
  check, so the layer that needs to hand off does not import the layer it hands off
  to, and which the harness itself calls after composing rather than leaving to each
  project: the guarantee is worth exactly as much as its least attentive adopter.
  Every child is resolved and every gate check passes before anything is
  written. `runtime.sweep_id` is supplied to the resolver so a child template can
  name its sweep.
- [x] 4.1h A sweep is extensible in place (D29): its identity comes from its
  directory rather than its definition, so growing an axis adds the children it
  gained and moves nothing — the relationship an augmenting command has to a run.
  `sweep_id` and `sweep_hash` separated as the glossary always had them; the index
  accumulates rather than being rewritten; a removed axis value retires its child
  instead of dropping it, and restoring the value adopts it back.
  - Deferred: the sweep resolving its own output block, peer to a run's. Preparation
    takes a sweep directory rather than deriving one, since a sweep-level output
    block needs a root schema of its own and that belongs with sweep-level reporting
    (4.1f), not with writing children. The pilot now does the project-side version of
    this — a templated `sweep.dir` in its own extended block, refused if it reads a
    path an axis varies — which is the shape to lift if this is ever pulled up.
- [x] 4.1i A project may extend the sweep block, and Honbu reads it through the
  type the project declared rather than through its own. The block was re-validated
  against `SweepBlock`, which is strict, so a project's own layout field — where its
  sweeps go, what to call this one — was refused as an extra input; the block was the
  single sweep definition only for Honbu's half of it. Strictness is unchanged: a
  subclass is strict too, so a misspelled key is still refused, against the schema
  that actually applies.
- [x] 4.1c Execution and status derived from the children's manifests: skip
  completed, resume every resumable state, uniform `--resolved <child> --resume`
  invocation, and stop/resume verified by test — including end to end against the
  demo, paused mid-sweep and resumed. The driver holds no state and reports none:
  where an interrupted pass stopped is derivable by refreshing, so returning its own
  account of it would be the state this design exists to not have. A missing child
  is reported rather than passed over; a retired one is neither run nor counted.
- [ ] 4.1d (Deferred to separate follow-up changes) Optional execution adapters:
  job submitter (`submitit`, `[slurm]`) and search-driven sweeper (`optuna`,
  `[search]`) built over the `child_argv` + `best.metrics` seam; sequential local
  execution stays in `4.1c` and `7.10`.
- [x] 4.1e Axis well-formedness (D24): the same-paths-per-option,
  no-path-claimed-twice and at-least-two-options rules; refusal rather than a
  skipped combination for a bad path; the refusal of an override contesting a swept
  path or one below it; the axis-wins warning emitted once per axis, returned as a
  string like every other Honbu warning rather than emitted; and the refusal of a
  group axis whose package path the base configuration also sets, detected by
  composing with the group dropped from the defaults list, which is the only form of
  the check that can tell a base-supplied key from one every option sets alike.
  `plan_sweep` runs the checks in fix-cost order and either refuses or returns a
  fully expanded plan.
- [x] 4.1g The directive marker (D28): a fourth exclusion from behavioral sources,
  claiming audience rather than provenance; `SweepDeclaration` as the one line a
  project adds; the block left optional rather than requirable; and verification at
  the point a sweep is asked for that the field is both declared and marked, since
  omitting the marker fails silently.
- [x] 4.1f Sweep manifest (D23): the accumulating index zone, the rebuildable
  progress cache with its refresh time, refresh-before-report, and tests that
  deleting *or corrupting* the cache changes neither what is reported nor what
  execution does. The two zones are two files, unlike a run's single manifest,
  because their durability rules are opposite: a permanent record written by
  preparation and a disposable cache written by whoever refreshed last, with the
  children owning the truth neither holds.
- [x] 4.5 Completion and the reserved surface (D25): a producing command's declared
  completion set defaulting to all steps; the `partial` state joining the resumable
  set; augmenting commands no longer recording started/finished or failing a
  completed run; `--init` removed with `initialize_run()` retained for sweep
  preparation; framework-option applicability derived per command and enforced at
  parse time, with help generated from the same derivation.
- [ ] 4.2 Run comparison engine (`compare_runs`: caller-declared gates, N-way
  config diff sharing `compare_configs`, objective ranking over `best.metrics`,
  graceful unranked listing of incomplete runs, sweep rollup).
- [ ] 4.3 Version-aware reconciliation (per-hash recipe & `encoder_version` gating,
  encoder re-hash, resolved-tree diff, key-compatible stored-source comparison
  minus `version`, defaulting to `on_unreconcilable="warn"` with opt-in `"refuse"`).
- [ ] 4.4 Reusable run compatibility gating (`check_run_compatibility` over D7
  reconciliation, supporting ensembling/merging/cross-run checks while projects
  own artifact combination).

- [x] 4.6 Config comparison (D26): operand references reusing the four
  configuration sources plus a run directory; N-way result distinguishing absent
  from null; the literal / authored / behavioral / by_hash levels with availability
  following from root type and registry; unavailable levels refused rather than
  downgraded; hash attribution with callable-built hashes reported opaque; a
  structured result plus one provided renderer.
- [x] 4.7 Config materialization (D27): closure from the compositor's recorded group
  selections with its internal groups excluded; project-declared scopes over roots
  and packaged resources; the create/skip/overwrite plan, dry run, additive default,
  explicit overwrite, and never deleting; functions plus optional command
  registration reserving no name.
- [x] 4.8 Local-versus-packaged drift, as an application of 4.6 over 4.7's two trees.

## 5. Release discipline and adoption

- [ ] 5.1 Guard test: a minor framework version bumps no hash recipe version and
  does not change the canonical encoder version.
- [x] 5.2 Adoption guide + reference: assemble a `RootConfig`, register commands,
  declare hashes, declare an objective, retype paths to the portable-path marker —
  one how-to page each, plus a tutorial that walks an empty directory to a resumable
  run, five concept pages, seven reference pages, and an ordered adoption guide for a
  project that already has a trainer.
  The structural decision is that **no sample is hand-written**. The scaffold written
  before this pass had already rotted — its central page told a reader to call
  `Application.register`, which does not exist, from a constructor missing a required
  argument — which is the copy-adaptation drift Honbu exists to remove, reappearing in
  its own documentation. So `examples/linefit` is a complete working project the suite
  runs end to end, and every sample is a snippet quoted from it: a renamed API fails
  the tests, and a renamed snippet section fails `mkdocs build --strict` (verified by
  breaking one). The API reference is generated from the docstrings, which already
  carry the reasoning, rather than restated.
- [ ] 5.3 Pilot adoption on one branch project (behind a dev branch), then the
  second, reconciling the shared modules against the framework.

## 6. Public package organization

- [x] 6.1 Breaking package refactor: replace `honbu.mechanics` and
  `honbu.capabilities` with the direct public areas `honbu.core`, `honbu.flow`,
  `honbu.sweeps`, `honbu.compare`, and `honbu.materialize`; split sweep
  declaration, axis interpretation, and planning into `definitions`, `axes`, and
  `plan`; preserve behavior and update tests with the new terminology.

## 7. Decoupled CLI, fork lineage, and interface cleanups (supersedes 3.7)

- [ ] 7.1 Core dependencies & layering guard (D20): replace unused `rich>=13`
  with `typer>=0.12` in core dependencies; extend `test_layering` to assert
  `honbu.core` imports no Hydra/OmegaConf/YAML/Typer, and neither `honbu.flow`
  nor `honbu.cli` top-level imports Typer (only `honbu.cli.presets` imports
  Typer).
- [ ] 7.2 Opt-in `HashedRuntimeConfig` preset (D21): export
  `HashedRuntimeConfig(RuntimeConfig)` in `honbu.core.blocks` pre-wired to
  `FromHash("config_hash")` and `config_id = "{runtime.config_hash:coolname}"`,
  keeping bare `RuntimeConfig` hash-free.
- [ ] 7.3 Pure `honbu.flow` & glue fixes (D15, D17, D18, D19):
  - Strip CLI fields (`options`, `positionals`, `applicable_options`,
    `ParsedInvocation`, `parse_invocation`) from `CommandSpec` and `honbu.flow`.
  - Remove `Application` and `dispatch_command` from `honbu.flow.harness`.
  - Rename config source `"continue"` → `"fork"` and `continue_artifact` →
    `fork_artifact` (optional default; caller-selectable artifact key recorded as
    both `artifact_key` and `artifact` in `manifest.parent`).
  - Expose typed `load_config(...)`, public `parent_record(...)`, and public
    `TemplateContext.now(extras=...)` (with `resolve(..., context=None)`
    defaulting to minting one).
  - Move `refuse_active_sweep_block` into `resolve()`.
  - Preserve resolution + target-preparation warnings on `Run.warnings`.
  - Make `honbu.core.targets` collision refusals flag-agnostic by default with an
    optional CLI hint override.
  - Parameterize `config_hash_name` (default `"config_hash"`) in `open_run` and
    `parent_record`.
- [ ] 7.4 Opt-in CLI patterns (`honbu.cli` & `honbu.cli.presets`, D19):
  - Create library-agnostic `honbu.cli`: `validate_override_tokens`,
    `split_dash_rule`, `select_config_source`, refusal exit helper (with optional
    structured-error hook), warning emitter, and `CONVENTIONAL_FLAGS`.
  - Create Typer-backed `honbu.cli.presets`: `HYDRA_CONTEXT_SETTINGS`, ready-made
    `Annotated` option aliases (`ConfigOpt`, `ResolvedOpt`, `ForkFromOpt`,
    `ForkArtifactOpt`, `ConfigDirsOpt`, `ClobberOpt`, `ResumeOpt`), customizable
    option factories, and optional mountable Typer command builders for
    materialization and comparison.
- [ ] 7.5 Peer operation alignment:
  - Rename `OperandSource` `"continue"` → `"composed"` in `honbu.compare.configs`.
  - Parameterize `child_argv` flag spellings in `honbu.sweeps.progress`.
- [ ] 7.6 Update `examples/linefit`, tests, and docs:
  - Port `examples/linefit/project.py` to Typer + `honbu.cli.presets` and
    `HashedRuntimeConfig`, keeping all doc snippet anchors live-tested.
  - Update `docs/how-to/commands.md` (including fixing `"create"` → `"produce"`),
    `docs/reference/cli.md`, `docs/reference/api.md`, and `docs/how-to/adopt.md`
    to match the Option B interface; verify `pytest` and `mkdocs build --strict`.
- [ ] 7.7 Materialization fixes & drift upgrades (D27):
  - Allow `ComposeSpec.local: str | None = "configs"` so packaged-only
    composition uses `local=None` instead of a sentinel directory hack.
  - Fix `closure_roots` to strip `@package` suffixes (`group@pkg` → `group`) and
    support external authored files (`MaterializationRoot.from_file(...)`)
    copying only their packaged dependencies while leaving the file in place.
  - Make `MaterializationScope` configs-only with honest field names
    (`roots`, `files`, `exclude`), dropping fixture `resources`, and exclude
    Python package plumbing (`__init__.py`, `__pycache__/`, `*.pyc`, `*.pyo`)
    by default during directory walks.
  - Unify `plan_materialization` / `plan_config_materialization` to derive the
    packaged root from `compose_spec` (with optional test override), and add
    `.skipped`, `.overwritten`, and `render_plan(plan)` on `MaterializationPlan`.
  - Fix `compare_materialized_config` so the local side shadows packaged defaults
    (working on partial local trees) and accepts `overrides`; add file-level
    `materialized_file_drift` + `render_file_drift` (`modified`, `unchanged`,
    `local_only`, `packaged_only`).
  - Add optional Typer materialization command builder in `honbu.cli.presets`,
    update `tests/test_materialize.py` and `docs/how-to/materialize.md`.
- [ ] 7.8 Config comparison fixes & pure-hash evaluation (D26):
  - Validate operands through `root_type` at `authored`, `behavioral`, and
    `by_hash` so schema defaults normalize cleanly (keeping `literal` raw).
  - Evaluate all `reads_inputs=False` declarations (`FieldHash`, `SourceHash`,
    `FannedHash`) at `by_hash` via `hashes.compute(..., input_reading=False)` for
    exact per-hash digests and source diffs; reserve `opaque_hashes` for
    `reads_inputs=True` declarations; refuse missing `command` when any
    `FieldHash` is command-scoped.
  - Fix `_is_excluded` wildcard segment matching and hyphenated container keys,
    preserve empty dicts `{}` and empty lists `[]` in `_flatten`, and unify
    `==` equality semantics across `differences` and `residual_paths`.
  - Clean up `ConfigOperand`: drop redundant `"run"` source (keeping `default`,
    `file`, `composed`, `resolved`), add per-operand `extra_dirs` and
    `config_index`, and update `tests/test_compare.py` and `docs/how-to/compare.md`.
- [ ] 7.9 Identity & hashing fixes and ergonomics (D4, D5, D18):
  - Add `versioned: bool = True` to `SourceHash` and `FannedHash` (defaulting to
    requiring `"version"` in built sources, allowing a versionless custom
    `config_hash` via `versioned=False`), and allow `config_hash` to optionally
    carry a version if a project wants one.
  - Forbid `FromHash` targeting `reads_inputs=True` hashes in
    `validate_identity_fields`, remove `CommandRegistry.deferred_paths` plumbing
    (keeping Phase 6 refusal for any `reads_inputs=False` hash returning `None`),
    and update tests that previously bound `FromHash("dataset_hash")`.
  - Add `Run.hash(name) -> str | None` and `Run.hash_entry(name) -> HashEntry | None`
    symmetric with `Run.artifact(name)`, returning resolved and completed deferred
    hashes (including fanned `<name>.<key>` entries) across fresh runs and `--resume`.
  - Export `project_fields` and add/export `project_model(model, *, include=None, exclude=())`
    in `honbu.core` so custom `SourceHash`/`FannedHash` builders automatically
    inherit marker-driven exclusions.
  - Guard `deterministic_id` with a `threading.Lock()` for thread-safe RNG access.
  - Add the missing pinned cross-project tricky-tree SHA-256 test in
    `tests/test_core.py` (completing `1.2`'s `Pinned mechanics` scenario) and fix
    doc drift (`docs/concepts/identity.md` `ByteHash` Deferred=`no`→`yes`;
    `docs/reference/hashes.md` `hashes/`→`config/hashes/`).
- [ ] 7.10 Sweeps bugs, `SweepOutputBlock`, compositor wiring, and execution ergonomics (D22–D24, D29):
  - Fix sequence-index traversal in `assign_path` (`prepare.py`) and `_lookup`
    (`plan.py`), unwrap `Optional`/`Annotated` in `declared_block_type`
    (`definitions.py`) via `traversable_submodel`, and normalize `/` + `@pkg` in
    `override_key`.
  - Refuse empty ordinary axis lists `[]`, empty nested axis mappings, and empty
    covariant options `{}` (keeping single-value ordinary axes allowed for CLI
    pinning).
  - Verify target directory stability (`r1.target == r2.target`) in
    `check_gate_is_stable`, fix the misleading template advice in
    `check_gate_distinguishes`, preserve deduplicated child `resolution.warnings`
    on `Preparation.warnings`, default `prepare_sweep(..., context=None)` to
    `TemplateContext.now()`, remove the stale pre-D29 docstring in `prepare.py`,
    and update `docs/how-to/sweeps.md` away from `APP.dispatch`.
  - Implement cross-field `@model_validator` combination skipping (`value_error`
    recorded in `Preparation.skipped` and `config/index.json["skipped"]` with a
    warning, refusing if zero valid children remain, while still refusing
    field-level type/literal/constraint typos).
  - Add opt-in `SweepOutputBlock(OutputBlock)` preset and `sweep_target` support
    on `prepare_sweep` (refusing `dir_template` referencing any swept axis path,
    persisting `config/composed.yaml`, `config/resolved.yaml`, and
    `config/index.json`), while keeping explicit `sweep_dir` override (persisting
    `config/composed.yaml` and `config/index.json`).
  - Accept `compose_spec` and `extra_dirs` directly on `plan_sweep` and
    `prepare_sweep` (and export `sweep_composers(...)`), automatically forwarding
    non-sweep overrides and `extra_dirs`, translating nested group paths
    (`a.b` → `a/b` and `~a/b`), and running `check_no_group_is_shadowed`, while
    keeping explicit `compose_child`/`compose_without` callbacks as escape hatches.
  - Upgrade `execute_sweep` with `on_error: Literal["stop", "continue"] = "stop"`
    (`ExecutionResult.failed`, always propagating `KeyboardInterrupt`/`SystemExit`),
    validated `only=` accepting child indices or gate values, and separate
    `SweepStatus.missing` (active) from `SweepStatus.retired_missing`.
- [ ] 7.11 Run lifecycle fixes & manifest hardening (D12, D14, D15):
  - Fix Phase 7 (`_phase7_leaves` in `resolve.py`): check `_authored` for leaf
    `dir`/`dir_template` (matching Phase 6), re-render templated leaves in
    `command.scope` on augmenting `--resolved` replays unless `leaf.dir` was
    explicitly overridden, collect `rendered.warnings`, pass `path_fields`, handle
    `is_cwd_relative`, and enforce `path_length_refusal` on anchored leaf dirs.
  - Refuse mutually exclusive `clobber=True, resume=True` and refuse `resume=True`
    on augmenting commands in `open_run` / `_prepare`.
  - Update `Run.skip()` to snapshot `manifest.satisfied_steps()` (`completed` and
    `not_applicable`) rather than only `completed_steps()`.
  - Harden `Manifest`: record `kind`/`message` on `"error"`/`"interrupted"`
    timeline events and clear top-level `failure` on `"resumed"`/`"finished"`/
    `"incomplete"`; derive `state` from the last reserved timeline event on
    `Manifest.load(..., derive_state=True)` with `derive_state=False` opt-out;
    update `record_progress(fields=None, /, **kwargs)` to merge incrementally and
    add `set_progress(fields=None, /, **kwargs)` for full replacement; add
    `artifact_key` to `record_parent`.
  - Protect producing run hashes: augmenting invocations with `config_index > 1`
    write their hashes in `config/<N>/hashes/` and `invocations[i]["config_hash"]`
    and only add newly introduced hash names to top-level `manifest["hashes"]`.
  - Harden `--resume`: preserve recorded `identities["run_id"]` when not
    overridden (preventing spurious `config/2/`), and refuse behavioral
    `config_hash` drift by default (`allow_config_drift=False`), directing callers
    to `--fork-from` or `--clobber`.
  - Fix edge cases: wrap `format(value, tail)` `ValueError` in `templates.py` into
    `TemplateError` and support sequence indices in `_lookup`; skip disabled
    `OutputLeaf` (`enabled=False`) in `_scope_dirs`; handle `resolved.is_file()`
    in `targets.prepare_scope` (matching `prepare_target`).
- [ ] 7.12 Strict schema & path portability fixes (D2, D9, D10, D18):
  - Mark `OutputBlock.dir_template` and `OutputLeaf.dir_template` with `DIRECTIVE`
    so neither `dir` nor `dir_template` enters behavioral digests or `behavioral`
    comparison (keeping `OutputBlock.dir` as authorable `PortablePath | None`).
  - Wire `is_absolute_uri` into `_phase2_input_paths`, `anchor`, and
    `Run.artifact` so Windows drive paths read on POSIX stay absolute; extend
    `_phase2_input_paths` to walk `WILDCARD` container paths (`dict[str, Block]`,
    `list[Block]`, `list[PortablePath]`, `dict[str, PortablePath]`).
  - Verify `output_root` carries `PathMarker` in `validate_root_contract`.
  - Update `backend_kwargs(block, *, mode: Literal["python", "json"] = "python",
    recursive: bool = False)` to preserve native Python types by default while
    supporting `mode="json"` and optional recursive `DEFER` omission.
  - Enforce refusal of non-null authored `RESOLVED_ONLY` fields in `resolve()`
    when `replay=False`; add explicit `sweep_id: str | None = None` to `resolve()`
    (used by `prepare_sweep` instead of mutating `composed`), and update test
    fixtures that typed authorable `dir` as `ResolvedPath`.
  - Fix doc & export drift: correct `IDENTITY` description in
    `docs/reference/markers.md`, export `CONFIG_HASH_NAME` from `honbu.core`, and
    refresh the stale docstring in `honbu/__init__.py`.
- [ ] 7.13 Passive `ProjectSpec`, curated top-level exports, & adoption pattern note (D1, D19):
  - Add passive frozen `ProjectSpec(root_type, compose_spec, hashes=...,
    templates=..., config_hash_name=...)` in `honbu.flow` (running
    `validate_root_contract` and `validate_identity_fields` on construction, with
    zero CLI or IoC coupling), accepted as an optional convenience across
    `load_config`, `resolve`, `open_run`, `compare_configs`, `prepare_sweep`,
    `plan_materialization`, and `honbu.cli.presets` alongside individual kwargs.
  - Curate top-level `honbu/__init__.py` to the ~25 everyday project-authoring
    symbols (`StrictModel`, `ExperimentConfig`, `RuntimeConfig`,
    `HashedRuntimeConfig`, `OutputBlock`, `OutputLeaf`, `PortablePath`,
    `ResolvedString`, `ResolvedPath`, `TemplatedString`, `TemplatedIdentity`,
    `DERIVED`, `DIRECTIVE`, `IDENTITY`, `PORTABLE_PATH`, `RESOLVED_ONLY`,
    `TEMPLATED`, `FromHash`, `DEFER`, `Deferred`, `backend_kwargs`, `FieldHash`,
    `SourceHash`, `ByteHash`, `FannedHash`, `HashRegistry`, `HashEntry`,
    `project_fields`, `project_model`, `validate_root_contract`,
    `validate_identity_fields`, `unmarked_path_fields`, and `HonbuError`
    subclasses), keeping lower-level walker/target/IO/template internals in
    `honbu.core.*` (updating internal tests/docs imports cleanly).
  - Document the derived-domain-field pattern (`RESOLVED_ONLY` without `DERIVED`
    populated by `@model_validator(mode="after")`, e.g. `dojo`'s
    `transforms.inference_pipeline`) in `docs/how-to/adopt.md` during the docs
    pass (`7.6` / post-pilot docs).
