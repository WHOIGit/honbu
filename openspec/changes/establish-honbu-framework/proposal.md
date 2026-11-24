## Why

Two sibling ML-training projects — `sati` (sliced object detection) and `dojo`
(image classification) — independently grew the *same* foundation: identically
named `config_loader/{compositor,conductor,resolver}.py`, the same strict
Pydantic schema discipline, the same Hydra composition grammar, the same
content-addressed identity/hashing family, the same collision-safe run/artifact
lifecycle. They grew it by copy-adaptation, and it drifted. Re-synchronizing one
project to the other's hash mechanics recently cost a multi-task breaking change
that was, in substance, hand-porting mechanics that should live in one place.

Honbu is that one place: the framework (the *honbu* — the headquarters that
defines the forms) whose mechanisms and style the branch projects follow, so the
foundation stays canonical by construction instead of by manual correction.

## What Changes

- **NEW** Establish `honbu`, an importable framework a training project depends
  on to define its subcommands, configuration composition, strict schemas,
  content identities, and output/run-artifact lifecycle — while the project
  keeps its own domain commands, schemas, backends, and results.
- Provide a **layered spine** a project adopts at whatever depth fits. One
  install carries every layer; a project opts out of a layer by not importing it,
  and only genuine execution backends are optional dependencies.
  - **Core**: canonical JSON, `hash_source`, deterministic
    human IDs, the strict-model base and field markers, marker walkers, atomic
    writes, output-path template rendering, collision validation.
  - **Flow**: the pure-Python `compose → load → resolve → collision-check →
    persist → work → complete` lifecycle, lifecycle-only command declarations,
    the config provenance bundle, and the live run manifest — with zero CLI
    parsing or hardcoded flag names.
  - **Operations & CLI patterns**: `cli` (opt-in CLI helpers plus `honbu.cli.presets`
    for Typer), `sweeps`, `compare`, and `materialize` are peer public package
    areas built on core/flow; projects fully own their CLI design and import
    whichever patterns fit without being forced to use them.
    (`sweeps`, `compare`, and `materialize` provide parameter sweeps, config/run
    comparison, compatibility gating, and configuration materialization.)
  - Composition and identity are **siblings** within this ordering, not stacked:
    neither depends on the other, and resolution is the first thing needing both.
- Make **sweeps stoppable**: preparation resolves and materializes every child
  run without performing work, and execution is a resumable loop whose only state
  is the children's own manifests — so a sweep survives a Ctrl-C, a scheduler
  timeout, or "I need the GPU for something else."
- Keep the project **in control**: the project holds the main loop and calls
  into the framework; the framework never inverts control. A project uses the
  depth it wants and hand-rolls the rest without fighting the framework.
- Define a **project-assembles-from-framework-parts** schema model: the
  framework exports strict base *block types* and the machinery; each project
  writes its own flat `RootConfig` from those block types plus its domain block
  types, and MAY subclass a base block to extend it with domain fields.
- Fix the **identity mechanics** as the non-negotiable core: sources project in
  the config's own flat key namespace, carry a `version` field, serialize
  through one frozen canonical encoder that refuses rather than coerces what it
  cannot encode unambiguously, and exclude portable-path and generated-identity
  fields by walking type markers. Each hash is a declaration and is persisted as
  its own artifact alongside the source it digests; which operations a hash gates
  is decided by the requesting operation, not recorded on the hash.
- Define a **version-aware reconciliation model** for comparing runs produced
  under different framework versions: gate on per-hash recipe version (never the
  framework version); reconcile config identity by diffing stored resolved
  configuration directly; reconcile encoder bumps and key-compatible domain
  identities from stored source objects; err toward warning by default, with
  explicit opt-in refusal for operations that produce fused artifacts.

## Scope

Honbu targets **run-once, config-driven, content-addressed supervised training
runs** of the shape `config × fixed-dataset → artifacts + metrics`, where runs
are independent and grouped and compared by their configuration identity. This
covers `sati` and `dojo` and their near neighbours (any single, independent,
config-driven function over a fixed dataset).

**Non-goals.** Any run whose shape is not a single, independent, config-driven
function over a fixed dataset — where the run is not run-once, where identity is
not primarily the configuration, or where a run's output is the training input
of another run — is out of scope by construction, and the framework does not try
to model it.

## Capabilities

### New Capabilities

- `config-and-cli`: Hydra composition (search precedence, experiment-group
  selection, mutually exclusive `default` / `file` / `resolved` / `fork`
  sources via a typed loader), bare-comma override refusal, and opt-in
  importable CLI patterns (`honbu.cli` helpers and `honbu.cli.presets` for
  Typer) while projects own their CLI surface.
- `strict-schema`: the strict-model base, field markers and walkers, the
  project-assembles-from-framework-block-types composition model with
  block-level extension, an opt-in `HashedRuntimeConfig` preset, and a
  root-contract guard.
- `identity-and-hashing`: the frozen canonical encoder, the `version`-stamped
  bare-namespace source projections, deterministic human IDs, marker-driven
  exclusion, the four hash declaration forms, and the per-hash artifact.
- `path-portability`: one sanitization rule for every platform, reported rather
  than silent, with absolute-reference recognition in both syntaxes and path
  length refused before writing.
- `run-lifecycle`: declared commands owning their write scope, the fixed
  resolution phase order, output-path templating and portable path resolution,
  scope-confined collision safety and resume, declared steps, the config
  provenance bundle, and the live run manifest with its invocation log.
- `config-comparison`: N-way comparison of configurations (`default`, `file`,
  `composed`, `resolved`) at four declared levels (`literal`, `authored`,
  `behavioral`, `by_hash`), normalizing schema defaults at schema-backed levels,
  computing all pure `reads_inputs=False` hashes for exact digests and source
  diffs, and reporting `reads_inputs=True` hashes as opaque.
- `config-materialization`: packaged defaults usable before a local tree exists,
  configs-only materialization of a declared closure into a working tree —
  planned, additive, never deleting — with divergence from the packaged original
  answered by composed and file-level drift comparison rather than a lockfile.
- `sweeps-and-comparison`: config-defined sweeps expanded by Honbu over the
  compositor's override parser rather than its multirun machinery — cartesian and
  paired axes, a declared child-distinguishing gate checked before anything is
  written, children indistinguishable from hand-run configurations, an immutable
  sweep index with its own output block, and sweep state derived from the
  children's manifests so execution is stoppable and resumable. Plus N-way run
  comparison, objective ranking, and reusable run compatibility gating
  (supporting ensembling, merging, and cross-run checks) with the version-aware
  reconciliation model.

### Modified Capabilities

<!-- None — greenfield framework. -->

## Impact

- New repository `honbu` with the layered package above. Core dependencies carry
  the compositor and `typer` (replacing unused `rich`), so every install can
  compose, persist, run, and import CLI presets without selecting an extra; the
  accepted cost is an inherited exact pin on the compositor's parser runtime.
  Job submitters and hyperparameter search engines stay optional.
- The public package layout is a deliberate breaking surface:
  `honbu.core`, `honbu.flow`, `honbu.cli` (including `honbu.cli.presets`),
  `honbu.sweeps`, `honbu.compare`, and `honbu.materialize`. The old
  `honbu.mechanics` and `honbu.capabilities` paths are removed rather than
  preserved as compatibility shims.
- Downstream: `sati` and `dojo` adopt Honbu as a dependency and re-express their
  `config_loader`, schema base, hashing, and lifecycle modules in terms of it,
  keeping only their domain blocks, commands, backends, and results. Adoption
  MAY be a large per-project refactor; that is acceptable.
- Establishes a release discipline: a framework minor version MUST NOT change
  any hash's recipe version (an enforceable guard), so runs stay comparable
  across minor bumps.
