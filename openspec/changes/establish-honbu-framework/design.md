## Context

`sati` and `dojo` are sibling ML-training applications that deliberately share a
design language. Their foundations converged on identical structure —
`config_loader/{compositor,conductor,resolver}.py` in both, the same strict
Pydantic schemas, the same Hydra Compose grammar, the same content-addressed
identity family, the same collision-safe run/artifact lifecycle — but they
converged by copy-adaptation, so they drift. The forcing example: one project's
hash mechanics (canonical JSON, a `version` convention, bare-namespace source
projections, marker-driven exclusion) had to be hand-ported onto the other in a
large breaking change. That labor is the recurring cost Honbu removes: it makes
the shared foundation canonical in one place, so branch projects inherit the
forms instead of re-deriving them.

Honbu is scoped to run-once, config-driven, content-addressed supervised
training runs of the shape `config × fixed-dataset → artifacts + metrics`, where
runs are independent and identified by their configuration. That shape is the
design's single load-bearing assumption; everything below assumes it.

## Goals / Non-Goals

**Goals**

- One canonical home for the mechanics both projects must not drift on:
  canonical JSON, identity/hashing, IDs, strict-model base, lifecycle.
- Keep branch projects in control — they call the framework; the framework does
  not invert control — so a project adopts the depth it wants and hand-rolls the
  rest without a fight.
- Make cross-cutting capabilities (sweeps, comparison, ensemble assessment)
  something you build once and both projects consume.
- Domain divergence lives entirely in project-declared parts; a new capability
  costs a declaration, not a fork.

**Non-Goals**

- Owning any project's domain schema, commands, backends, or results.
- Modelling runs that are not a single, independent, config-driven function over
  a fixed dataset. Such shapes are out by construction and not accommodated.
- Prescribing a training engine, a data format, or a metric.

## Decisions

### D1 — Layered spine + organs; the project holds the loop

Three layers, each opt-in below the first:

```
  L3  CAPABILITIES   sweeps · run-comparison · ensemble-assessment ·
      (opt-in)       inspect scaffolding
  L2  FLOW           compose→resolve→collide→persist→work→complete ·
      (opt-in)       CLI dash-grammar · provenance bundle · run manifest
  L1  MECHANICS      canonical JSON · hash_source · IDs · strict-model base ·
      (always)       markers + walkers · atomic writes · template rendering ·
                     collision validation
  organs plug at every layer: schema blocks · command→active-blocks map ·
  work functions · domain hashes · output topology · backends
```

The project is the main loop and reaches into the spine (it is not called by an
inversion-of-control frame). Rationale: a framework that must accommodate more
than one domain cannot dictate the flow; the escape hatch must be "don't call
that part," not "fight the frame." **Placement rule:** if duplicating a mechanism
caused drift, it is L1 and non-negotiable (canonical JSON, the `version`
convention, marker exclusion).

### D2 — Schema composition: project assembles from framework block *types*

The framework exports strict base *block types* (e.g. an experiment block, a
runtime block carrying the run/sweep identities, a results block, output blocks)
and the machinery; each project writes its own flat `RootConfig` from those block
types plus its domain block types. A `validate_root_contract` guard checks the
required base blocks are present, each is (a subclass of) its framework type, and
subclass blocks only *add* fields.

```
  from honbu import RuntimeConfig, ResultsConfig, OutputBlock, validate_root_contract
  class RootConfig(StrictModel):
      runtime: RuntimeConfig        # framework type → cannot drift
      results: ResultsConfig
      slicing: SlicingConfig        # domain type
      ...
  validate_root_contract(RootConfig)
```

Rationale: this is the only option that (a) keeps the flat top-level namespace
the identity mechanics and Hydra groups depend on, (b) prevents drift by sharing
base *definitions*, (c) keeps the project in control with a plain, fully-typed
class, and (d) needs no inheritance/MRO/generic/factory machinery — the marker
walkers operate on whatever tree the project assembles. Alternatives considered:
subclassing one framework `RootConfig` (couples the type hierarchy, forces a
rigid base/domain line); a generic domain payload (re-nests the namespace — the
exact wrapper the identity work removes); a dynamic model factory (destroys
static typing both projects rely on).

### D3 — Block-level extension is the escape hatch

Because the project chooses each field's *type*, it may substitute a subclass of
a base block to extend it with domain fields — extending precisely the block that
needs it, not the whole root. Extended fields must carry the framework markers
themselves (the walker traverses the actual subclass; the framework cannot guess
intent), and extension means *add* fields, never retype or remove base fields
(the contract guard enforces this). Evidence it is needed, not hypothetical: one
project's runtime block already carries training-infra fields the other lacks —
extension isolates those in a subclass while the shared identity core stays in
the framework type and cannot drift.

### D4 — Identity mechanics are the non-negotiable core

Sources project in the config's own flat key namespace (field names, no synthetic
wrapper), serialize through one shared canonical-JSON path (sorted keys, compact
separators, floats normalized then JSON-rendered, non-finite tokenized), and
carry a `version` field; the configuration identity carries no version token (its
identity is its content). Portable-path and generated/derived-identity fields are
excluded by walking type markers, never a hand-maintained list. Deterministic
human IDs derive from a hash, domain-seeded. This is L1: identical in both
projects by construction, so the class of drift that motivated Honbu cannot recur.

### D5 — Each hash declares a comparison role

The split between hashes that *gate* an operation (a mismatch forbids it) and
hashes that are *provenance / group-by axes* becomes declared data on each hash —
`{ role: gate | provenance, gates: [operations] }` — not documentation prose. A
generic engine then answers "comparable? ensemblable?" for any project by walking
each run's hash block and checking the hashes whose role gates the requested
operation. The framework never needs to know a project's specific hashes; each
hash carries its own role.

### D6 — One engine for sweeps and comparison; a clean work-fn seam

A sweep declares a base config, axes (config path + values), a strategy, an
output template, and an objective (a metric key + direction). The framework
expands the axes, composes/resolves each child, assigns child identities, runs
each through the L2 lifecycle calling the project's work function, writes a sweep
manifest, collects canonical metrics, and groups by configuration identity —
without knowing what the work function trains. N-way config comparison and a
sweep's per-axis rollup are the *same* operation, so sweeps and comparison are one
engine with two entry points (axes known vs. discovered). Ensemble assessment is
the same shape: a stricter gate set, a project-supplied combine work function, and
the same objective. The domain surface of all three is a work function plus two
declarations (objective, hash roles) — never a fork.

### D7 — Version-aware reconciliation for cross-version comparison

Comparison gates on **per-hash recipe version**, never the framework version (a
framework change that leaves a hash's recipe untouched leaves those runs fully
comparable). When a relevant hash's recipe version differs between two runs:

```
  vX == vY                         → compare recorded digests
  byte-identity hash (no recipe)   → always comparable
  config identity                  → diff the stored resolved configuration trees
                                     directly (version-proof; the resolved config
                                     is the whole structure)
  domain identity, sources stored, → re-hash the stored source objects under one
    compatible shape                 chosen recipe and compare
  shape incompatible / sources gone → advisory op (compare): warn and proceed
                                       artifact-producing op (ensemble): refuse
```

Two reconciliation substrates: the resolved configuration (owns configuration
identity and yields a free structural diff — always available) and the stored
source objects (own the domain identities, whose inputs are not in the config).
This retroactively justifies storing sources alongside digests: it is what makes
graceful cross-version comparison possible. Release rule: a minor framework
version MUST NOT bump any hash recipe version — an enforceable guard test — so
"compatible minor-to-minor" is a precise, testable promise.

## Risks / Trade-offs

- [Adoption is a real refactor for two working codebases] → Accepted; a big-bang
  per-project refactor on a dev branch is the intended path. The payoff is the
  end of manual drift-correction.
- [The framework could over-reach beyond its scope] → The single-shape
  assumption (run-once, config-driven, fixed-dataset, config-identified) is
  stated as the boundary; anything outside it is not modelled.
- [A base-block change ripples into both projects] → Contained by D2/D3: shared
  base *types* change in one place; per-project extensions are subclasses;
  version pinning lets each project adopt on its own schedule.
- [Contract enforced by a guard, not the type system] → A project could omit a
  base block; caught at import/test time by `validate_root_contract`, cheaply.
- [Cross-version comparison could silently mislead] → D7's ladder makes most
  cases exact (tree diff / source re-hash) and reserves the naked warning for
  advisory operations only; artifact-producing operations refuse rather than
  fuse.

## Open Questions

- Migration ordering — which project adopts first, and whether the framework's
  first release is extracted from one project's current modules or written fresh
  against both.
- Where the shared canonical-JSON helper's cross-project equivalence is pinned
  (a pinned guard test in each consumer vs. a single shared fixture).
- How far the inspect/reporting scaffolding is shared vs. left per-project.
