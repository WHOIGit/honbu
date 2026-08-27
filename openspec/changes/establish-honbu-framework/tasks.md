## 1. L1 — Mechanics (the non-negotiable core)

- [x] 1.1 `errors`: `HonbuError` / `RefusalError` base, so a user-facing refusal
  exits cleanly without a traceback.
- [x] 1.2 `canonical`: frozen canonical encoding (bytes primitive), refusals with
  dotted paths, `hash_source`, frozen encoder version, and a pinned tricky-tree
  guard test.
- [x] 1.3 `model`: strict base (forbid extras, validate assignment and defaults,
  strip whitespace, non-finite survive serialization), the deferral sentinel and
  `backend_kwargs`.
- [x] 1.4 `markers`: three markers and composed aliases, container-aware marker
  walkers with memoization, wildcard-expanding pruner, unmarked-path guard.
- [x] 1.5 `paths`: union sanitization for every platform, sanitization warnings,
  absolute-reference detection in both syntaxes, platform path-length budget.
- [x] 1.6 `io`: atomic write (unique temp + rename, residue-free), text and
  artifact-JSON wrappers kept separate from the canonical encoder.
- [x] 1.7 `templates`: pure renderer, token grammar including the invocation
  namespace, extensible function registry with `emits_path`, sanitization
  exemption for path-marked sources, and the token-path extractor the command
  registry reads.
- [x] 1.8 `hashes`: the three declaration forms, declared-path validation at
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
- [x] 3.2 `manifest`: the live single-writer run record — five states derived
  from an append-only timeline, invocations with config indices, artifacts,
  progress, best, lineage, auto-flushing atomically.
- [x] 3.3 `compose`: exported search-path assembly with defined precedence, the
  Hydra call behind a lazy import, four mutually exclusive configuration sources
  declared per command, and the dash-rule argv split with declared option arity
  and positionals.
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

## 4. L3 — Capabilities

- [ ] 4.0 Flatten the install and reverse the reserved hash name (D20, D21):
  compositor, config library and YAML into core dependencies; delete the three
  "install the extra" refusal branches; add the layering guard test asserting no
  mechanics module imports them; drop the `FromHash` binding from the base runtime
  block and default the configuration-identity field to null; add
  `submitit`/`optuna` extras.
- [ ] 4.1 Sweep block and expansion (D22): the open axis mapping in the root
  schema, flat-and-nested key normalization recorded per child, cartesian and
  paired axes, the bare-top-level-comma refusal pointing at the block, and the
  sweep-definition digest under the standard exclusions.
- [ ] 4.1a The child-distinguishing gate: declared identifier, stability
  verification by resolving under two invocation contexts, the
  must-appear-in-the-output-template rule, and a refusal naming colliding children
  and the axes that failed to separate them.
- [ ] 4.1b Preparation and the sweep directory: materialize every child in the
  initialized state running nothing, strip the sweep block from each child so its
  digests match a hand-run configuration, idempotent re-preparation, the sweep
  output block with its `config/` holding the immutable index, and a producing
  command's refusal of an active sweep block.
- [ ] 4.1c Execution and status derived from the children's manifests: skip
  completed, resume every resumable state, no sweep-level progress record, uniform
  `--resolved <child> --resume` invocation, and stop/resume verified by test.
- [ ] 4.1d Optional execution adapters: local sequential needing no extra, a job
  submitter, and a search-driven sweeper reading its objective from a child's
  recorded best metrics rather than a work-function return value.
- [ ] 4.2 Comparison engine (operation-declared gates, N-way residual diff,
  objective rank) sharing the sweep rollup.
- [ ] 4.3 Version-aware reconciliation (per-hash recipe gating, resolved-tree
  diff, source re-hash, advisory-warn vs. artifact-refuse).
- [ ] 4.4 Ensemble assessment (stricter gates, combine work-fn seam, objective).

## 5. Release discipline and adoption

- [ ] 5.1 Guard test: a minor framework version bumps no hash recipe version and
  does not change the canonical encoder version.
- [ ] 5.2 Adoption guide + reference: assemble a `RootConfig`, register commands,
  declare hashes, declare an objective, retype paths to the portable-path marker.
- [ ] 5.3 Pilot adoption on one branch project (behind a dev branch), then the
  second, reconciling the shared modules against the framework.
