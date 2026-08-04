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
- Provide a **layered spine** a project adopts at whatever depth fits:
  - **Mechanics** (always): canonical JSON, `hash_source`, deterministic
    human IDs, the strict-model base and field markers, marker walkers, atomic
    writes, output-path template rendering, collision validation.
  - **Flow** (opt-in): the `compose → resolve → collision-check → persist →
    work → complete` command lifecycle, the dash-rule CLI grammar, the config
    provenance bundle, and the run manifest.
  - **Capabilities** (opt-in): parameter sweeps, run comparison, and ensemble
    assessment — built once on the mechanics/flow and shared by every project.
- Keep the project **in control**: the project holds the main loop and calls
  into the framework; the framework never inverts control. A project uses the
  depth it wants and hand-rolls the rest without fighting the framework.
- Define a **project-assembles-from-framework-parts** schema model: the
  framework exports strict base *block types* and the machinery; each project
  writes its own flat `RootConfig` from those block types plus its domain block
  types, and MAY subclass a base block to extend it with domain fields.
- Fix the **identity mechanics** as the non-negotiable core: sources project in
  the config's own flat key namespace, carry a `version` field, serialize
  through one shared canonical-JSON path, and exclude portable-path and
  generated-identity fields by walking type markers. Each hash declares its
  comparison **role** (gate vs. provenance) so comparison and ensembling gate
  generically without domain knowledge.
- Define a **version-aware reconciliation model** for comparing runs produced
  under different framework versions: gate on per-hash recipe version (never the
  framework version); reconcile config identity by diffing stored resolved
  configuration directly; reconcile domain identities from stored source
  objects; degrade to a warning for advisory operations and refuse for
  operations that produce fused artifacts.

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
  selection, mutually exclusive roots), the dash-rule override grammar, and the
  installed command surface a project exposes.
- `strict-schema`: the strict-model base, field markers and walkers, and the
  project-assembles-from-framework-block-types composition model with
  block-level extension and a root-contract guard.
- `identity-and-hashing`: canonical JSON, the `version`-stamped bare-namespace
  source projections, deterministic human IDs, marker-driven exclusion, and the
  per-hash comparison-role registry.
- `run-lifecycle`: command-aware side-effect-free resolution, output-path
  templating and resolution, collision safety, the config provenance bundle, and
  the atomic run manifest.
- `sweeps-and-comparison`: parameter sweeps, N-way run comparison, and ensemble
  assessment — one gate/diff/rank engine over the shared identity family, with
  the version-aware reconciliation model.

### Modified Capabilities

<!-- None — greenfield framework. -->

## Impact

- New repository `honbu` with the layered package above.
- Downstream: `sati` and `dojo` adopt Honbu as a dependency and re-express their
  `config_loader`, schema base, hashing, and lifecycle modules in terms of it,
  keeping only their domain blocks, commands, backends, and results. Adoption
  MAY be a large per-project refactor; that is acceptable.
- Establishes a release discipline: a framework minor version MUST NOT change
  any hash's recipe version (an enforceable guard), so runs stay comparable
  across minor bumps.
