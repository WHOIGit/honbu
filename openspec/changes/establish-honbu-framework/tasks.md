## 1. L1 — Mechanics (the non-negotiable core)

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
  absolute-reference detection in both syntaxes, platform path-length budget.
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

## 4. L3 — Capabilities

- [x] 4.0 Flatten the install and reverse the reserved hash name (D20, D21):
  compositor, config library and YAML into core dependencies; delete the three
  "install the extra" refusal branches; add the layering guard test asserting no
  mechanics module imports them; drop the `FromHash` binding from the base runtime
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
  to. Every child is resolved and every gate check passes before anything is
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
    (4.1f), not with writing children.
- [x] 4.1c Execution and status derived from the children's manifests: skip
  completed, resume every resumable state, uniform `--resolved <child> --resume`
  invocation, and stop/resume verified by test — including end to end against the
  demo, paused mid-sweep and resumed. The driver holds no state and reports none:
  where an interrupted pass stopped is derivable by refreshing, so returning its own
  account of it would be the state this design exists to not have. A missing child
  is reported rather than passed over; a retired one is neither run nor counted.
- [ ] 4.1d Optional execution adapters: local sequential needing no extra, a job
  submitter, and a search-driven sweeper reading its objective from a child's
  recorded best metrics rather than a work-function return value.
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
- [ ] 4.2 Comparison engine (operation-declared gates, N-way residual diff,
  objective rank) sharing the sweep rollup.
- [ ] 4.3 Version-aware reconciliation (per-hash recipe gating, resolved-tree
  diff, source re-hash, advisory-warn vs. artifact-refuse).
- [ ] 4.4 Ensemble assessment (stricter gates, combine work-fn seam, objective).

- [ ] 4.6 Config comparison (D26): operand references reusing the four
  configuration sources plus a run directory; N-way result distinguishing absent
  from null; the literal / authored / behavioral / by_hash levels with availability
  following from root type and registry; unavailable levels refused rather than
  downgraded; hash attribution with callable-built hashes reported opaque; a
  structured result plus one provided renderer.
- [ ] 4.7 Config materialization (D27): closure from the compositor's recorded group
  selections with its internal groups excluded; project-declared scopes over roots
  and packaged resources; the create/skip/overwrite plan, dry run, additive default,
  explicit overwrite, and never deleting; functions plus optional command
  registration reserving no name.
- [ ] 4.8 Local-versus-packaged drift, as an application of 4.6 over 4.7's two trees.

## 5. Release discipline and adoption

- [ ] 5.1 Guard test: a minor framework version bumps no hash recipe version and
  does not change the canonical encoder version.
- [ ] 5.2 Adoption guide + reference: assemble a `RootConfig`, register commands,
  declare hashes, declare an objective, retype paths to the portable-path marker.
- [ ] 5.3 Pilot adoption on one branch project (behind a dev branch), then the
  second, reconciling the shared modules against the framework.
