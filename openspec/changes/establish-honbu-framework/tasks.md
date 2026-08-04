## 1. L1 — Mechanics (the non-negotiable core)

- [ ] 1.1 Canonical JSON + `hash_source` + `sha256` helpers, with a pinned
  cross-project equivalence guard test.
- [ ] 1.2 Strict model base + field markers (path / hash-derived / resolved-only)
  + marker walkers + bare-path guard.
- [ ] 1.3 Bare-namespace versioned source builder and marker-driven exclusion
  primitives.
- [ ] 1.4 Deterministic domain-seeded human IDs + fresh IDs.
- [ ] 1.5 Atomic writes, output-path template rendering, collision validation.

## 2. Schema composition

- [ ] 2.1 Export base block types (experiment, runtime with run/sweep identities,
  results with objective, output blocks).
- [ ] 2.2 `validate_root_contract` (required blocks present, is-a base type,
  subclass blocks only add fields).
- [ ] 2.3 Per-hash comparison-role registry (gate | provenance, gated operations).

## 3. L2 — Flow

- [ ] 3.1 Compositor (Hydra Compose, search precedence, experiment selection,
  exclusive roots) and dash-rule token routing.
- [ ] 3.2 Command-aware, side-effect-free resolver (identities, templates, path
  resolution, deferred input-derived identities for inspection).
- [ ] 3.3 Collision-safe target handling + config provenance bundle + atomic run
  manifest.
- [ ] 3.4 Command harness (register subcommands, thread the flow, clean refusal
  exit).

## 4. L3 — Capabilities

- [ ] 4.1 Sweep engine (axis expansion, strategy, child resolution + lifecycle,
  sweep manifest, group-by identity, skip invalid combinations).
- [ ] 4.2 Comparison engine (gate check on roles, N-way residual diff, objective
  rank) sharing the sweep rollup.
- [ ] 4.3 Version-aware reconciliation (per-hash recipe gating, resolved-tree
  diff, source re-hash, advisory-warn vs. artifact-refuse).
- [ ] 4.4 Ensemble assessment (stricter gates, combine work-fn seam, objective).

## 5. Release discipline and adoption

- [ ] 5.1 Guard test: a minor framework version bumps no hash recipe version.
- [ ] 5.2 Adoption guide + reference: assemble a `RootConfig`, register commands,
  declare hashes with roles, declare an objective.
- [ ] 5.3 Pilot adoption on one branch project (behind a dev branch), then the
  second, reconciling the shared modules against the framework.
