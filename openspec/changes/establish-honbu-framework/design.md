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
- Make cross-cutting capabilities (sweeps, comparison, compatibility gating)
  something you build once and both projects consume.
- Domain divergence lives entirely in project-declared parts; a new capability
  costs a declaration, not a fork.

**Non-Goals**

- Owning any project's domain schema, commands, backends, or results.
- Modelling runs that are not a single, independent, config-driven function over
  a fixed dataset. Such shapes are out by construction and not accommodated.
- Prescribing a training engine, a data format, or a metric.

## Decisions

### D1 — Core, flow, and direct operation areas; the project holds the loop

Two dependency layers and three direct operation areas are opt-in by *import* —
see D20, which flattened the install:

```
  honbu.cli          opt-in CLI patterns (dash-rule, source selection, refusal exit)
                     · honbu.cli.presets (opt-in Typer option & command presets)
  honbu.sweeps       declaration · planning · preparation · execution adapters
  honbu.compare      run/config comparison · reconciliation · compatibility gating
  honbu.materialize  config-tree materialization · local/package drift
             ↑
  honbu.flow         pure-Python compose→load→resolve→collide→persist→work→complete ·
                     lifecycle-only CommandSpec · provenance bundle · run manifest
             ↑
  honbu.core         canonical JSON · hash_source · IDs · strict-model base ·
                     markers + walkers · atomic writes · template rendering ·
                     collision validation
```

The project is the main loop and reaches into these areas (it is not called by an
inversion-of-control frame). Rationale: a framework that must accommodate more
than one domain cannot dictate the flow; the escape hatch must be "don't call
that part," not "fight the frame." **Placement rule:** if duplicating a mechanism
caused drift, it is core and non-negotiable (canonical JSON, the `version`
convention, marker exclusion).

`core` and `flow` state the dependency direction. `cli`, `sweeps`, `compare`, and
`materialize` are peer public areas rather than a catch-all layer: each can build
on core/flow, but lower layers never import them. The names are intentionally
breaking — no compatibility modules preserve `honbu.mechanics` or
`honbu.capabilities`.

### D2 — Schema composition: project assembles from framework block *types*

The framework exports strict base *block types* and the machinery; each project
writes its own flat `RootConfig` from those block types plus its domain block
types. Exactly four things are requirable, because they are the only top-level
structures both sibling projects genuinely share: an **experiment** block, a
**runtime** block carrying the run identities, an **output root**, and at least
one **output block**. Everything else is domain. In particular a "results" block
is *not* requirable — the two projects have blocks by that name holding entirely
unrelated fields, and treating the coincidence as a contract would force one
project's results semantics onto the other for no benefit.

```
  from honbu import RuntimeConfig, OutputBlock, PortablePath, validate_root_contract
  class RootConfig(StrictModel):
      experiment: ExperimentConfig      # framework type → cannot drift
      runtime: RuntimeConfig
      output_root: PortablePath
      training_outputs: TrainingOutputs # project subclass of OutputBlock
      slicing: SlicingConfig            # domain type
      ...
  validate_root_contract(RootConfig)
```

The guard checks *presence, base type, and `PathMarker` on `output_root` only*.
`OutputBlock.dir_template` and `OutputLeaf.dir_template` carry `DIRECTIVE` so
neither `dir` nor `dir_template` enters behavioral digests or `behavioral`
comparison (while `dir` stays authorable `PortablePath | None`, not
`RESOLVED_ONLY`). See D3 for why the guard stops there.

Rationale: this is the only option that (a) keeps the flat top-level namespace
the identity mechanics and Hydra groups depend on, (b) prevents drift by sharing
base *definitions*, (c) keeps the project in control with a plain, fully-typed
class, and (d) needs no inheritance/MRO/generic/factory machinery — the marker
walkers operate on whatever tree the project assembles. Alternatives considered:
subclassing one framework `RootConfig` (couples the type hierarchy, forces a
rigid base/domain line); a generic domain payload (re-nests the namespace — the
exact wrapper the identity work removes); a dynamic model factory (destroys
static typing both projects rely on).

### D3 — Block-level extension is the escape hatch, and it is not policed

Because the project chooses each field's *type*, it may substitute a subclass of
a base block to extend it with domain fields — extending precisely the block that
needs it, not the whole root. Extended fields must carry the framework markers
themselves (the walker traverses the actual subclass; the framework cannot guess
intent). Evidence it is needed, not hypothetical: one project's runtime block
already carries training-infra fields the other lacks — extension isolates those
in a subclass while the shared identity core stays in the framework type.

An earlier draft had the contract guard enforce "add fields, never retype or
remove", and additionally police constraint changes behind an explicit
narrowing marker. Both are dropped. The add-only rule forbade something one
project already depends on: five of its output blocks subclass a base block
purely to override `dir_template`'s default, which requires restating the whole
field. And the constraint policing bought little — a changed constraint cannot
alter a digest (it changes what is *rejectable*, not what is encoded) and
announces itself behaviourally the same day, unlike the failures Honbu exists to
prevent.

So the guard checks presence and base type, and stops. A project that redeclares
a base field and drops its marker gets the behaviour it asked for; the reference
documentation explains what markers do and `unmarked_path_fields()` remains
available as a report-only check a project can assert in its own tests. Giving
projects the power to define the fields they need, with guidelines rather than
guardrails, is the deliberate trade.

### D4 — Identity mechanics are the non-negotiable core

Sources project in the config's own flat key namespace (field names, no synthetic
wrapper), serialize through one shared canonical-JSON path, and domain recipes
carry a `version` field by default. The configuration identity conventionally
omits a version token (its identity is its content), though a project may opt in
to versioning it; `SourceHash` and `FannedHash` expose `versioned: bool = True`
so a versionless custom source (such as a custom `config_hash` substituting
checkpoint identity) opts out cleanly while domain recipes remain version-checked
by default. Portable-path, derived, templated, and directive fields are excluded
by walking type markers — through mapping values and sequence elements as well as
direct submodels — never a hand-maintained list; `honbu.core` exports
`project_fields` and `project_model` so custom `SourceHash`/`FannedHash` builders
inherit marker exclusions automatically. Deterministic human IDs derive from a
hash, domain-seeded and thread-safe (guarding shared generator RNG state). This
is L1: identical in both projects by construction, so the class of drift that
motivated Honbu cannot recur.

The encoder is deliberately narrow. Sorted keys and compact separators make
authored order irrelevant; floats normalize to twelve significant digits and
negative zero folds to zero; strings are UTF-8 with no Unicode normalization, so
a digest means "these bytes." Everything it cannot encode without guessing —
non-string mapping keys, non-finite floats, non-JSON scalars — is **refused with
the offending dotted path**, not coerced and not tokenized. Refusing rather than
tokenizing is what let the alternative encodings go: a tagged binary stream (or
canonical CBOR) buys native support for exactly the cases we have decided are
bugs, at the cost of an encoding no human and no other tool can read.

### D5 — Hashes are declared; gating authority belongs to the operation

Each hash is a *declaration* in one of four forms: selected configuration fields
(`FieldHash` — the drift-prone case, so it is declarative data: an include set,
optionally per-command, plus per-hash excludes), a project-built source
(`SourceHash`, carrying its own recipe version by default via `versioned=True`),
a digest over file bytes (`ByteHash`), or a fanned-out declaration (`FannedHash`).
Field selection alone covers `config_hash` and almost nothing else — every domain
hash in both projects synthesizes or normalizes something — so the callable form
is not an escape hatch, it is the common case.

What a hash does **not** carry is which operations it gates. An earlier draft put
`{role, gates}` on each hash and had the engine walk a run's recorded hash block.
That is the wrong home: it freezes a *policy* into a historical artifact, so
changing your mind about what an ensemble requires would mean rewriting old runs.
Instead the requesting operation names the hashes it gates, resolved against the
live registry, and a consumer ignores hash names it does not recognize. The
framework still needs no domain knowledge; it just asks the operation instead of
the artifact.

A hash declared as input-reading (`reads_inputs=True`) is always **deferred** past
resolution — whether or not its inputs happen to be present — which is what makes
an optional byte-reading identity (scan the materialized dataset) a normal absence
rather than a degraded run, and what lets read-only inspection resolve everything
else. Because deferred hashes are run-time facts rather than static config fields,
they cannot bind a `FromHash` field; they are completed via `run.complete_hash()`
and read via `run.hash()` or `run.hash_entry()` (D18).

### D6 — One engine for sweeps and comparison; a clean work-fn seam

A sweep declares a gate, ordinary/group axes, and optional covariant axes in the
sweep block (D22–D24); preparation expands the matrix, resolves every child (with
optional `SweepOutputBlock` resolution or an explicit `sweep_dir`), checks the
gate and target-template stability, skips cross-field model-validator rejections,
and writes each valid child in `initialized` state without running work, while
local sequential execution is a stateless resumable loop over the accumulating
sweep index (with external `submitit`/`optuna` adapters deferred to follow-up
changes).

N-way configuration comparison (`compare_configs`) and run comparison
(`compare_runs`, including a sweep's per-axis rollup) share one engine: caller-
declared gate hashes, N-way configuration diffing, and objective ranking over each
run's recorded `best.metrics` (listing incomplete or unranked runs gracefully with
their state rather than aborting the report).

An earlier draft also specified a framework-owned `ensemble_assess` wrapper taking
a project `combine` work function. That is replaced by a more general primitive:
**`check_run_compatibility(runs, gates=..., on_unreconcilable=...)`**. Combining
predictions (averaging softmax logits vs. Weighted Box Fusion over tiled boxes) is
100% domain code; Honbu's reusable contribution is verifying gate agreement and
version reconciliation, which serves ensembling, model merging, distillation, and
cross-run validation equally without baking an ensemble concept into the framework.

### D7 — Version-aware reconciliation for cross-version comparison

Comparison gates on **per-hash recipe version** (and `encoder_version`), never the
framework version (a framework change that leaves a hash's recipe untouched leaves
those runs fully comparable). When a relevant hash's recipe version or encoder
version differs between runs:

```
  vX == vY and same encoder        → compare recorded digests directly
  byte-identity hash (no source)   → always comparable (raw file SHA-256)
  encoder_version bump only        → re-hash stored source objects under current encoder
  config identity (FieldHash)      → diff stored config/resolved.yaml trees directly
  domain identity, stored sources  → strip "version" and compare stored source dicts
    have compatible key shapes       directly (same keys and canonical values)
  incompatible source shapes or    → err toward warning by default (warn and proceed);
    missing sources                  callers producing fused artifacts opt in to refuse
```

Note the honest boundary on domain identities: a stored `SourceHash` artifact holds
the *output* of the old recipe, not the raw input config or dataset. Stored
sources therefore cleanly reconcile encoder bumps and key-compatible recipe
revisions, while newly added or renamed source keys cannot be fabricated from an
older artifact and surface as unreconcilable. Erring toward warning by default
keeps historical comparison usable, while `on_unreconcilable="refuse"` gives
artifact-producing workflows (such as ensembling) strict protection when requested.
Release rule: a minor framework version MUST NOT bump any hash recipe version — an
enforceable guard test — so "compatible minor-to-minor" is a precise, testable
promise.

### D8 — One artifact per hash; the encoder is versioned outside every digest

Each computed hash is persisted as its own file, `config/hashes/<name>.json`,
recording `{name, value, version, provenance, encoder_version, source}`. Stable
filenames rather than content-addressed ones, because comparing two runs by hand
is the thing you actually do when a digest surprises you, and identical paths make
that a plain `diff`.

A declaration may **fan out**: one project already keys a hash per model head, so
a single name yields one entry per key. Those persist as
`config/hashes/<name>.<key>.json`, or `<name>.<index>.json` where the fanned
collection is a sequence with no natural key. The key becomes a filename, so it is
sanitized as a path component, and two keys that sanitize to the same filename are
refused rather than allowed to overwrite one another — silently overwriting would
leave a plausible-looking artifact for a head that never got one.

Each entry records its own key alongside its name, so a reader addresses
`head_hash.dorsal` without parsing a filename to find where the name ends. A fanned
name cannot appear on a `FromHash` field, refused at construction: one field cannot
hold many digests. An absent `source` *means* the digest was taken over bytes,
so no separate kind flag is needed; `provenance` is a free-form project label
(dojo's `uri_size` / `uri_etag` / `manifest_content` ladder) that Honbu records and
never interprets.

Storing sources per hash is what makes D7's cross-version reconciliation possible
without the old code: reconciliation re-hashes what is on disk instead of
re-deriving a retired projection. Honbu imposes no size policy — a project that
does not want a huge listing in every run either declares the hash to digest a
nested digest instead of the expansion, or archives the expansion with the dataset
product. Dataset identity is entirely the project's: whether to scan, how, and
whether a byte-reading identity exists at all.

The canonical encoder has a recipe too, and per-hash versions cannot express it —
change the float format and every digest in existence moves with no version
recording it. So the encoder version is frozen, recorded in each hash artifact and
the run manifest, and present in no digest and not in the resolved configuration.
Changing it is a major framework version that bumps every recipe.

### D9 — One path-sanitization rule for every platform

Artifact path components are sanitized to the **union** of POSIX and Windows
rules everywhere, not to the running platform's rules. Per-platform sanitization
(dojo's current behavior) keeps existing Linux directory names byte-stable, but it
means one configuration produces different directory names on different machines —
unacceptable for configuration defaults committed to a repository and shared. When
sanitization changes a value, resolution warns naming both forms, so the rename is
visible once rather than discovered later.

Path length is refused, never truncated: on a platform enforcing a limit, a run
directory that leaves too little budget for the artifacts written beneath it fails
at resolution, before any write. Sanitized components never carry identity — that
invariant is inherited from dojo, and it is what lets path handling stay
platform-aware while digests stay platform-independent.

### D10 — Absence carries no meaning

`None` means null. "Apply the backend library's own default" is spelled with an
explicit deferral token (`DEFER`) that survives serialization and appears in
behavioral sources, so a run that deferred and a run that pinned the same value
are visibly different runs with different digests. `backend_kwargs(block, *,
mode="python", recursive=False)` omits `DEFER` entries while defaulting to
`mode="python"` (preserving native tuples/`Path` when forwarding directly to
Python backend libraries, with `mode="json"` available if a JSON-native dict is
needed). The alternative — absence as meaning — was already in use and already
cost a project a knob it could no longer turn off, because the sentinel consumed
the only spare value.

This also settles a subtler question: Pydantic's set-fields tracking cannot be
used to recover what a user authored, because resolution assigns fields and
revalidates the tree, and both mark everything as explicitly set. The authored
record is the composed configuration the provenance bundle persists. Making that a
stated rule costs nothing, and it stops the distinction from being rediscovered
as a bug.

Corollary worth stating in the adoption guide: deferring to a library default is
an identity hole. Two runs a year apart that both defer carry the same digest and
can behave differently. Pin values; defer only knowingly.

### D11 — Resolution is a fixed phase pipeline

Resolution computes fields from other computed fields — a path template naming a
derived identity that came from a hash, an output leaf anchored to a run directory
that was itself just rendered. Rather than a declared dependency graph or a lazy
pull model, the order is a **fixed list Honbu owns**:

```
  1 validate composed → RootConfig
  2 resolve input paths to absolute
  3 compute config-only hashes         (filtered to the command's declared set)
  4 assign identities from hashes      (digests into the fields declaring them)
  5 render template-valued fields       (so the target may reference them)
  6 render the command's target template
  7 resolve output leaves against the target directory
  8 revalidate
```

It holds for every case in both projects, including the one that nearly breaks it:
identities derived from identities. Every id derives from a *hash*, never from
another id, so splitting 3 and 4 leaves phase 4 order-free internally. Phases 2
and 3 are independent because paths never enter a digest (D9) — if they did,
absolutizing one would make digests machine-dependent.

The alternatives were rejected on legibility, not capability. A declared graph
turns a wrong `requires` into a wrong order with no error, and degrades messages
to "cycle: a→b→c". A lazy pull model computes only what is needed but gives
resolution no linear narrative — and resolution's output is a persisted artifact
someone reads at 2am asking why a digest changed. A numbered list answers that; a
graph does not. The cost is that a future dependency running backwards through the
list requires renumbering, which is a framework change.

Sweeps are not a phase: a sweep expands one composed configuration into N, each
resolved independently, so it is a compose-time transform.

### D12 — The command owns its write scope; two command shapes

`--clobber` originally cleared the run directory. That is the wrong scope: one
project uses it to mean "rewrite the figures," and a flag cannot distinguish blast
radii that differ by three orders of magnitude. The scope comes from the command
declaration instead.

Commands come in two shapes. A **producing** command creates a run directory and
owns it. An **augmenting** command writes into an existing one and owns only its
declared leaves — evaluating a finished run, re-rendering figures, exporting. An
augmenting command does not re-render the run-directory template; it inherits the
resolved directory from the existing manifest, appends to that manifest, and
`--clobber` can reach only the leaves it declared.

Leaf-level templating completes the picture: an override that changes a named hash
can flow into a leaf template (`results/{inference_hash:coolname}`), so evaluating
against a different dataset writes a new leaf rather than colliding with the old
one. Under an augmenting `--resolved` replay, Phase 7 re-renders templated leaves
in the command's declared `scope` unless the caller explicitly pinned a literal
`leaf.dir` override (while checking authored leaf fields, collecting warnings,
enforcing path-length limits, and skipping disabled `OutputLeaf` entries during
scope preparation). Both result sets coexist in one run directory, each with its
own recorded identity.

`--force` is dropped. Its value depended entirely on the run-directory template:
with the timestamped defaults both projects use it never fires, and with a
deterministic template it fires on every deliberate re-run and becomes reflex.
Describing the occupant in the refusal — "completed run, 40 epochs, best map50
0.613" — protects the same mistake with information instead of ceremony, and the
producing/augmenting split already removed most of what it guarded.

### D13 — Steps are declared by the project, scoped and recorded by Honbu

A training command is usually a sequence: prepare, preflight, fit, evaluate. Honbu
does not run that sequence — the project holds the loop (D1) — but it does scope
and record it. A step is declared on the command with its own write scope, and
entered as a context manager around the project's own function calls. Honbu
enforces the scope, appends step boundaries to the timeline, and refuses an
undeclared name (a typo would otherwise create a phantom step that resume silently
re-runs) and nesting (which would turn "which steps completed" from set membership
into a tree walk).

A step's "scope" governs what Honbu *clears* and what it records. It is not a
filesystem sandbox — nothing prevents project code writing elsewhere — and saying
so is better than implying a guarantee that does not exist.

Honbu ships **no** step names; the vocabulary is entirely the project's. An
exception inside a step fails the whole command and stops the remaining steps;
steps already recorded complete stay complete, which is what lets `--resume` skip
them. Partial writes inside the failed step are left in place as debugging
material.

What happens to those writes when the step runs again is **declared on the step**,
because only the project knows which of two opposite things its output is. A
dataset-preparation step that wrote half a manifest holds spoiled work that must
be discarded. A training step's checkpoints *are* the resume state: clearing them
turns "continue from epoch 10" into "start over", which is exactly the failure a
resume exists to prevent. An earlier draft cleared unconditionally and would have
destroyed a checkpoint on every resume.

**Continuing is the default.** The asymmetry decides it: wrongly restarting a
continue-step destroys hours of irrecoverable work silently, while wrongly
continuing a restart-step produces a visibly wrong result the step itself usually
trips over. The destructive option is the one you type, consistent with `--clobber`
and with containment.

Resume also covers the case where nothing has run yet — a directory holding only a
persisted configuration is in `initialized`, which is resumable, so starting the
first step in a later invocation is the same mechanism rather than a special one.
That matters for schedulers: a job that times out under SIGTERM leaves the state
`running`, which is likewise resumable, so the next allocation picks up where the
last stopped.

### D14 — One manifest, written live, holding a log of invocations

`manifest.json` at the top of the run directory is both the state record and the
index other operations locate resources through. It is a live object the project
mutates during work — registering artifacts, progress and the best epoch — and it
auto-flushes atomically on every mutation, because its whole value is that an
interrupted run still has a useful one. Honbu never decides which epoch is
"best"; the project calls `record_best` and Honbu stores what it is given, since
anything else would prescribe a metric contract.

That removes the need for work to return anything: the best epoch's metrics *are*
the comparison substrate, and artifacts are registered as they appear rather than
at the end (an interruption at epoch 40 must still name `checkpoints/last.pt`).

Five states — `initialized`, `running`, `completed`, `interrupted`, `failed` —
plus `partial` (D25), where `interrupted` and `failed` are discriminated by *who
stopped it*, an external signal versus an internal exception. Resumability is not
the discriminator: an OOM crash is often resumable too. `state` is a materialized
view, always derived from the last reserved timeline event and never written
independently — including on `Manifest.load(..., derive_state=True)` (a backward
scan with an explicit `derive_state=False` opt-out) so it cannot drift; the
timeline itself is an open append-only log the project can add domain events to.
Failure `kind` and `message` are recorded on the failure timeline event as well as
top-level `failure`, and top-level `failure` clears on resume or completion so a
resumed run does not carry a stale crash. `record_progress` merges keys
incrementally (accepting a mapping or kwargs) while `set_progress` replaces the
progress mapping wholesale.

A run directory accumulates invocations — augmenting commands are invocations —
so the manifest carries an `invocations` array and timeline events index into it.
`cli.txt` is dropped: two files that must agree is the one-writer problem again.
Each invocation references a config index, deduplicated on the canonical bytes of
the resolved configuration rather than on `config_hash`, so two invocations
differing only in a hash-excluded field stay distinguishable. Configs persist at
`config/` for the first and `config/<N>/` thereafter, keeping the obvious path
obvious. Top-level `manifest["hashes"]` preserves the producing run's digests: an
augmenting invocation with `config_index > 1` writes its own hashes under
`config/<N>/hashes/` and `invocations[i]["config_hash"]`, only adding newly
introduced hash names to top-level `manifest["hashes"]` without overwriting the
producing run's hashes.

### D15 — Forking a completed run produces a new run with recorded lineage

Extending training from a finished run — same trajectory, more epochs, or branching
off a checkpoint — is tempting to model in place. It is refused. A directory whose
artifacts came from one configuration for 100 epochs and another for 50 more has no
honest `config_hash`, and the truthful answer to "what produced this checkpoint" is
a sequence, which a single manifest cannot express. For the same reason, resuming
an unfinished run (`resume=True`) refuses behavioral `config_hash` drift by
default (`allow_config_drift=False`), directing the caller to fork or clobber
instead, preserves the run's recorded `run_id` when not overridden (avoiding
spurious `config/2/` allocation on default-config resume), and treats `clobber`
and `resume` as mutually exclusive. Both projects already spell warm-starting as a
first-class config concept (`weights.source = "checkpoint"`), so the capability is
not lost, only relocated.

**Forking** (`source="fork"`, conventional flag `--fork-from <run_dir>`) therefore
reads that run's **composed** configuration as the base, applies overrides, resolves
a **new** directory, and records `parent: {run_dir, config_hash, artifact_key, artifact}`.

*Why "fork" instead of "continue".* An earlier draft spelled this `--continue-from`.
In practice, `--resume` is already described as continuing an unfinished run in
place, so `--continue-from` collided directly with `--resume` while doing the
opposite (creating a new directory). `fork` makes creating an independent child
run unmistakable.

*Why `parent.artifact` stays required — and caller-selectable.* If a caller only
wants to reuse a prior run's authored config from scratch, they can already compose
`<run>/config/composed.yaml` directly without claiming lineage. Keeping
`parent.artifact` required ensures `manifest.parent` unambiguously means
artifact/state lineage rather than config copying. Because runs commonly register
multiple checkpoints (e.g. `best_checkpoint` and `last_checkpoint`), the artifact
key is **selectable at call time** via `parent_record(run_dir, artifact=...)`,
falling back to an optional default `CommandSpec.fork_artifact`, and recording both
`artifact_key` and its path. Because the project owns its CLI flow (D19), it can
also read `parent["artifact"]` before `resolve()` to wire the checkpoint path into
its config automatically.

The composed form rather than the resolved one, because resolution absolutizes
input paths: a resolved `./datasets/coco8` is stored as
`/home/you/datasets/coco8`, which is correct on the machine that produced it and
absent on the cluster node you meant to fork on. The authored form re-absolutizes
wherever it runs. The accepted cost is that it holds only what was authored, so a
schema default that moved since the parent ran is silently picked up; the parent's
recorded `config_hash` makes that detectable if a project cares to check.
Resolved-only fields are stripped as well, as a safety net for a project that
authors into one.

This is the sharp line between the two sources. A replay (`resolved`) keeps
everything, so the parent's literal output directory wins and the invocation lands
in that same run. A fork must not inherit that directory, or two configurations'
artifacts end up in one place with an honest identity for neither.

Copying the parent's other files — metrics, results, the manifest — is deliberately
not done. It would make the child's records describe someone else's run: copied
metrics would span two configurations under one `config_hash`, and a copied
manifest would hand the child the parent's identities and timeline outright. The
lineage pointer already reaches the parent's files in place, and only the project
knows which of them are continuable state rather than a record of a different run.
Whether the child restores full training state or only loads weights is the
project's business and shows up in the child's own configuration; Honbu records the
link and nothing about its semantics. Lineage is wanted by comparison regardless.

### D16 — The resolved configuration is portable; the runtime view is absolute

An earlier requirement made every output path absolute in the resolved
configuration. That is reversed: a path authored as absolute or
working-directory-relative is stored absolute, while one authored bare-relative
(or rendered from a template) is stored **unchanged**, anchored to the output root
— and an output leaf stays relative to its block. Moving a runs tree then keeps
those paths correct. The encoding is lossless in the direction that matters: since
cwd-relative paths become absolute, any relative value in a resolved configuration
is unambiguously output-root-relative.

The cost is that a stored path is no longer directly openable, which is a quiet way
to write to the wrong place. So resolution returns two views of one configuration:
`res.config` with every path joined and absolute, and `res.portable` as persisted.
The obvious name is the usable one. They are the same configuration — paths are
excluded from digests, so their digests are identical, and that equality is worth
asserting in a test. An `AnchoredPath` type carrying its own base was considered
and rejected: the anchor is context rather than data, so it does not survive
`model_validate`, `model_copy`, `deepcopy` or pickle, and it disappears silently.

### D17 — Honbu owns the compose call; group selection is just an override

Composition is one of the three modules that drifted by name in both projects, so
leaving it out would abandon a stated goal. Honbu therefore makes the Hydra
Compose call, owning the part that is genuinely fiddly and genuinely duplicated:
assembling `hydra.searchpath` with a defined order (repeatable explicit
directories, then a project-local directory, then packaged defaults). It also
exports that assembly, so a project needing multirun, structured configs or a
custom resolver drives Hydra itself with Honbu's search path rather than fighting
the wrapper.

One constraint is Hydra's, verified rather than assumed: the directory passed as
the *primary* config directory outranks every `hydra.searchpath` entry for groups
of the same name. So the primary directory is the highest-precedence one that
actually contains the primary config, and a `--config-dir` overlay shadows only
groups the primary directory lacks. An overlay meaning to win outright ships its
own primary config, which promotes it. This is exactly what the common case wants
— a local directory holding `config.yaml` shadowing packaged defaults — but it is
weaker than "explicit always wins", and stating it is better than discovering it.

Owning the call prescribes far less than it first appears. A Compose call needs a
search path, a config name and overrides; group names, directory layout and
defaults lists all live in the project's own YAML. In particular there is **no
named-experiment root source**. `experiment=reef-v3` is not special to Hydra — it
works because experiment files carry a `# @package _global_` header, a convention
any group may adopt and none must. So `data=nes-hf` and `training.epochs=50` are
the same kind of token, and the model is a *default root plus overrides* rather
than a family of selectors.

That leaves four config sources, mutually exclusive, with overrides allowed on top
of any of them: the default config name (`default`), an authored file (`file`), a
prior run's resolved configuration (`resolved`, given as a file or run directory),
and a parent run to fork from (`fork`). A command **declares which of them it
accepts**, which lets an augmenting command refuse a freshly composed root with a
message naming the fix instead of failing later at collision time, and lets a
command needing no configuration declare none. `honbu.flow` exposes a typed
`load_config(...)` loader for these sources with no CLI coupling.

Replaying a resolved configuration would otherwise trip the both-set refusal on
`dir` and `dir_template`, since a resolved artifact carries both. It does not,
because that refusal exists to catch *authoring* ambiguity: under a replay `dir`
wins and the check is skipped. This is also why a replay lands in the original
directory while a composed configuration produces a fresh run — the literal `dir`
pins it, reusing a feature rather than adding a special case.

Token routing (the dash-rule separating options from Hydra overrides) lives in
opt-in `honbu.cli`, not in `honbu.flow` — see D19.

### D18 — Resolution is pure and returns two objects; identities are declared on the field

Resolution performs no filesystem writes, which is stated as a requirement and
was quietly violated by an earlier sketch that hung `step()` and `complete_hash()`
on the resolution result. Those write, so they cannot live there. Resolution
returns a **`Resolution`** — the two configuration views, the computed hash
entries, the deferred set, the resolved target, the warnings — and the lifecycle
constructs a **`Run`** around it once the target is prepared. `Run` owns the
manifest and every write; work receives it and reads `run.config` directly. The
split also makes read-only inspection honest: it gets a `Resolution`, and no write
method is in reach.

Assigning a derived identity into the configuration is declared **on the field**,
with a marker naming a non-deferred, non-fanned hash:

```
  model_config_hash: Annotated[ResolvedString, FromHash("model_config_hash")] = None
```

rather than in a side table of dotted paths. There is no path to mistype, the
existing marker walkers already find such fields (containers included), and a
project subclass that adds one is picked up automatically. Only the hash *name*
remains a string, cross-checked against the hash registry at construction:
`validate_identity_fields` refuses unknown hashes, fanned hashes (one field
cannot hold many digests), non-resolved-only fields, and **deferred
(`reads_inputs=True`) hashes**.

Banning `FromHash` on `reads_inputs=True` hashes prevents a silent state split:
`config/resolved.yaml` is written in Wave 1 before work starts (and rewriting it
mid-run would shift `resolved_digest()` dedup), so a `FromHash` field targeting a
deferred hash would remain `None` in memory right after `run.complete_hash()` and
stay `null` in `config/resolved.yaml` forever, contradicting `manifest.json`. It
also eliminates `CommandRegistry.deferred_paths` plumbing, making it structurally
impossible for a target template to name a deferred hash field. Deferred hashes
are run-time facts persisted in `config/hashes/` and `manifest.json`, completed
via `run.complete_hash()` and read via `run.hash(name)` / `run.hash_entry(name)`
(symmetric with `run.artifact(name)` and surviving `--resume`).

The marker assigns the **digest**. Any human-readable form is a template concern —
`{runtime.config_hash:coolname}` — so a separate id *field* is usually redundant
and earns its place only when the id is a domain value used outside templates. An
earlier draft put a `form` argument on the declaration, duplicating what the token
function already does at render time.

Both alternatives were rejected. A side-table declaration reintroduces the typo
class that forced construction-time path validation on the hash registry. A
project **hook** between phases is worse still, and the reasons generalize: a hook
may write any field, including one already hashed in phase 3, so the recorded
digest can come to describe a configuration that never ran — unvalidatable by
construction, where a marker can be checked to target only resolved-only,
non-deferred fields. Ordering between two hook-assigned identities is invisible,
so a refactor that swaps two lines yields a template rendering `None` into a
directory name with nothing raising. And a single hook cannot span phases, so
needing one identity before rendering and another after grows the phase list by
two undocumented entries. There are no hooks anywhere in L2: the project already
holds the loop and owns work, so there is no gap for one to fill.

This yields a clean asymmetry worth stating outright: **output templates are
discovered by type** — anything typed `OutputBlock` or `OutputLeaf` carries the
directory pair, so phases 5 and 6 walk the tree and need no declaration — while
**identities are declared by marker**, because no type says "a hash goes here."

Two consequences to record rather than rediscover. Resolution is a function of the
composed configuration, the command, the invocation context *and*, for an
augmenting command, the existing run manifest it inherits its directory from — so
it is not a function of the configuration alone. `TemplateContext.now(extras=...)`
is public (and `resolve(..., context=None)` mints one when omitted), `resolve()`
enforces both the active-sweep-block refusal and the non-replay refusal of
authored `RESOLVED_ONLY` fields (receiving `sweep_id: str | None = None`
explicitly rather than via `composed`), Phase 2 absolutizes `PortablePath` fields
including inside `dict`/`list` containers while honoring `is_absolute_uri` across
POSIX/Windows syntaxes, and `Run.warnings` preserves both resolution and
target-preparation warnings rather than dropping them. And phase 8 revalidates the
tree, so every project `model_validator(mode="after")` runs a second time against
a tree that now has identities assigned and paths absolute; a validator asserting
something true only of an unresolved tree will pass composed and fail resolved.

### D19 — Projects own their CLI; Honbu exports opt-in CLI patterns (`honbu.cli`, `honbu.cli.presets`)

An earlier draft shipped Honbu's own `Application` dispatcher and
`dispatch_command(handler, raw_tokens)` harness inside `honbu.flow`, baking CLI
options, positionals, and six hardcoded framework flags into `CommandSpec`. That
design is **reverted** after auditing it against real `dojo` and `sati` commands.

Why the harness failed usability in practice:

1. **It broke real CLI libraries even via its escape hatch.** `dispatch_command`
   demanded raw unparsed argv tokens and ran its own parser, neutralizing Typer's
   rich `--help`, typed options (`Path(exists=True)`, `Literal` choices), subgroup
   help, and shell completion — and `dispatch_command` dropped project options and
   positionals before calling `WorkFn(run)` or `InspectFn(resolution)`.
2. **It welded away mid-pipeline seams.** Real commands legitimately act between
   `resolve()` and `open_run()` — verifying a checkpoint inference contract before
   creating an output directory (`sati infer`/`eval`/`export`), checking idempotent
   cache reuse before collision refusal (`sati prepare`), or selecting which
   `CommandSpec` to resolve dynamically (`sati inspect config --command`).
3. **It contradicted D1 and broke symmetry.** `honbu.compare`, `honbu.materialize`,
   and `honbu.sweeps` are all library-first and never prescribe command names or
   flag spellings; forcing CLI ownership inside `honbu.flow` made it the sole
   exception, and growing `OptionSpec` to fix its ergonomics would have turned
   Honbu into a half-baked re-implementation of Click/Typer.

**The new split:**

* **`honbu.flow` is 100% CLI-free.** `CommandSpec` declares lifecycle and schema
  semantics only (no CLI options, positionals, or flag names). `load_config`,
  `resolve`, `open_run`, `initialize_run`, and `parent_record` accept plain typed
  Python arguments. `honbu.core.targets` emits flag-agnostic refusal wording by
  default with optional CLI hint overrides.
* **`honbu.cli` provides opt-in, library-agnostic terminal helpers** (importing no
  Typer): `validate_override_tokens` (dash-rule + bare-comma and brace-quoting
  diagnostics), `split_dash_rule`, flag-agnostic `select_config_source`, refusal
  exit handling (`HonbuError` prints cleanly and exits non-zero without a
  traceback, optional structured-error hook for `--format json`, defects keep
  tracebacks intact), warning emission, and documented `CONVENTIONAL_FLAGS`
  (`--config`, `--resolved`, `--fork-from`, `--fork-artifact`, `--config-dir`,
  `--clobber`, `--resume`).
* **`honbu.cli.presets` provides opt-in Typer presets**: `HYDRA_CONTEXT_SETTINGS`,
  ready-made `Annotated` option aliases (`ConfigOpt`, `ResolvedOpt`,
  `ForkFromOpt`, `ForkArtifactOpt`, `ConfigDirsOpt`, `ClobberOpt`, `ResumeOpt`),
  customizable option factories so a project can rename any flag or help string
  while keeping identical semantics, and optional mountable Typer command builders
  for materialization and comparison. Keeping the public path `honbu.cli.presets`
  keeps imports crisp today while allowing expansion into submodules later without
  breaking callers.
* **`Application` / `SimpleApp` and `dispatch_command` are removed.** Real projects
  and `examples/linefit` build directly on Typer + `honbu.cli.presets` (or any CLI
  library they prefer). Guided project scaffolding belongs in an onboarding agent
  skill (a separate change) rather than a bespoke CLI framework inside Honbu.
* **Passive `ProjectSpec` & curated top-level exports.** To eliminate repeating
  `(root_type, compose_spec, hashes, templates, config_hash_name)` across dozens
  of call sites without reintroducing inversion of control, `honbu.flow` exports a
  passive frozen `ProjectSpec` dataclass (validating `validate_root_contract` and
  `validate_identity_fields` on construction) accepted as an optional convenience
  parameter across `flow`, `sweeps`, `compare`, `materialize`, and `cli.presets`
  alongside direct kwargs. Top-level `honbu/__init__.py` is curated to the ~25
  everyday schema, marker, hash, and error symbols while lower-level walker,
  target, and IO helpers remain under `honbu.core.*`.

### D20 — One install; the layers are import boundaries, not extras

Hydra, OmegaConf, PyYAML, and Typer live in the core dependencies (`typer`
replaces unused `rich`, which Typer brings along). `pip install honbu` gives every
layer and CLI presets with no extras matrix, and a project opts out of any area by
not importing it.

The extras were justified by dependency weight, and that justification was real:
`hydra-core` → `omegaconf` → `antlr4-python3-runtime==4.9.*`, an exact pin on a
2021 release that every consumer now inherits. **This is the accepted cost**, and
it is the one thing that could force the decision back: a project needing antlr
4.10+ for something else has no resolution. `typer` itself is small, pure Python,
and introduces no pinning hazard. Accepted because the alternative is worse in a
way that shows up on everyone's first command rather than in one environment's
dependency solve — lazy-import branches that exist only to say "install the extra",
firing during someone's first invocation, plus an extras matrix presented at
adoption time to exactly the person least equipped to choose.

Two things this gives up, one of which is bought back:

*Structural enforcement of the layering.* It was previously **impossible** for
`core/` to import a composition primitive, because the package was not
installed. That is now convention. It is bought back with a test asserting that no
module under `core/` imports hydra, omegaconf, yaml, or typer, and neither
`flow/` nor `cli/` top-level imports typer (only `cli/presets` imports typer) —
cheap, and treated as non-optional, because "two modules that should be
independent quietly grow a dependency" is the drift Honbu exists to remove.

*The zero-dependency core case.* A project wanting only the canonical encoder
and the markers now installs antlr and typer to get them. Not bought back; judged
not worth an extras matrix.

Genuine launchers stay optional, because they are not capabilities Honbu
implements: `submitit` (`[slurm]`), `optuna` (`[search]`), and anything
Slurm-shaped are extras, delivered in separate follow-up changes after the core
and local sequential sweep engine stabilize. See D22 for why they are used as
libraries rather than as Hydra plugins.

### D21 — The configuration identity is optional and project-bound, with an opt-in preset

Bare `RuntimeConfig` keeps `config_hash` unbound and `config_id` defaulting to
null. A project that wants no configuration identity uses `RuntimeConfig`
unmodified; neither the run lifecycle nor the manifest requires one.

This preserves the resolution of an earlier review pass: binding `config_hash` on
bare `RuntimeConfig` made declaring a hash *mandatory*, contradicting "the
framework SHALL NOT infer identities from hashes." However, nearly every real
project *does* want the conventional `config_hash` → `config_id` coolname wiring,
and forcing each one to hand-write the same marker-annotated subclass invites
copy-paste mistakes. Honbu therefore exports an opt-in **`HashedRuntimeConfig`**
subclass of `RuntimeConfig` pre-wired to `CONFIG_HASH_NAME` (`config_hash` bound
with `FromHash("config_hash")` and `config_id = "{runtime.config_hash:coolname}"`).
Projects wanting a different hash name subclass `RuntimeConfig` directly.

`config_hash` remains the *conventional* name, not a reservation: `open_run`,
`parent_record`, and comparison all take the identity/hash name as a parameter
(defaulting to `CONFIG_HASH_NAME`) rather than hardcoding `"config_hash"`.

### D22 — Honbu expands sweeps itself, over Hydra's parser, never over multirun

Sweep expansion is Honbu's. It reuses exactly one piece of Hydra — the
`OverridesParser`, standalone, with no `ConfigLoader` and no Hydra run — and none
of the multirun machinery.

The reason is not the output-directory conflict, which is smaller than it looks
(`hydra.sweep.dir` and `hydra.sweep.subdir` are configurable and `hydra.job.chdir`
has defaulted to false since 1.2). It is that **multirun holds its progress in
memory**. `BasicSweeper.sweep` keeps position in `self.overrides` and
`self.batch_index` under `while not self.is_done()`, and the only thing it writes
to the sweep directory is `multirun.yaml` — the configuration, not the position.
Killed at child 7 of 12, a re-run starts at child 0 and retrains the seven that
finished. There is no hook that fixes this from outside, because there is nowhere
to read "where was I" from. For a sweep that must be stoppable and resumable
(D23), that is the whole requirement, not a gap around the edges.

The plugin ecosystem is not the loss it appears to be, because **the capability is
in the library, not in the plugin.** `submitit` and `optuna` are independent
packages; `hydra-submitit-launcher` is an adapter translating a `hydra:` node into
`submitit.AutoExecutor` parameters. Honbu translates its own sweep block instead,
which is less code, not more. And driving a Hydra `Launcher` directly is
ill-advised on its own terms: `Launcher.setup` demands a live `HydraContext` and a
`DictConfig` carrying a `hydra:` node, and the submitit launcher pickles the task
function and re-enters `hydra.core.utils.run_job` on the worker — smuggling a
Honbu run through Hydra's idea of a job, with two frameworks claiming to own what
a run is.

Optuna additionally cannot be used through its plugin at all: the sweeper reads
the task function's **return value** as the objective, and a Honbu work function
returns `None` by design (D14). Used directly, Optuna's ask/tell API separates
proposing parameters from running the job and its RDB storage makes the study
resumable — so the objective is read from a child's recorded `best.metrics` and
survives the driver process dying, which the plugin's version does not. The
`submitit` and `optuna` adapters themselves will land in separate follow-up
changes built over this exact `child_argv` + `best.metrics` seam while
`establish-honbu-framework` establishes the core and local sequential sweep
engine.

Consequently Honbu refuses a **bare top-level comma** on an ordinary
configuration override. In Hydra's grammar `epochs=10,20` is a multirun sweep, and
it is one character away from `epochs=[10,20]`, a list value, while meaning
something entirely different. Axes come only from the sweep block; the refusal
names it.

### D23 — A sweep's state is derived from its children's manifests

The sweep manifest has two zones. The **index** of prepared children is immutable
after preparation. The **progress** zone is a *cache*: a child's state is its own
`manifest.json`, the sweep's state is the aggregate, and every cached value is
rebuildable by rescanning. So the driver holds no authoritative state, and killing
it loses nothing.

The cache earns its place by being readable without running anything — `cat` the
file and see 8/12. It pays for that with a stale-read hazard that has to be made
visible rather than papered over: a child run by a Slurm array updates its own
manifest and nothing else, so the zone carries the time it was last refreshed, and
anything reporting sweep state refreshes first. A count with no timestamp would
assert currency it does not have.

Preparation materializes every child up front: expand the matrix, resolve each
combination (skipping project cross-field `@model_validator` rejections into
`Preparation.skipped` and `index.json["skipped"]` while still refusing field-level
type/literal/constraint typos), check the gate and target-template stability, then
write each child's run directory in the `initialized` state without running
anything. Projects wanting config-driven sweep output resolution opt into
**`SweepOutputBlock(OutputBlock)`** (peer to `training_outputs`, refusing any
`dir_template` referencing a swept axis path, persisting `config/composed.yaml`,
`config/resolved.yaml`, and `config/index.json`, with `OutputLeaf.enabled`
disabling reporting leaves), while callers may still pass an explicit `sweep_dir`
(persisting `config/composed.yaml` and `config/index.json`). Execution is then a
loop over the index (`execute_sweep` with `on_error="stop" | "continue"` and
validated `only=` accepting indices or gate values), and it needs no new
vocabulary because Honbu's existing resumable set is already exactly right:
`initialized`, `running`, `partial`, `interrupted`, and `failed` are resumed,
`completed` is skipped. `running` being resumable is what covers a Slurm timeout,
where SIGTERM does not raise and the child is left mid-flight.

Two consequences worth stating. Every child is reachable by one uniform resume
invocation against its run directory — conventionally `--resolved <child> --resume`,
with `child_argv` parameterizing the flag spellings and `execute_sweep` taking a
project-supplied runner callback — whether it never started or died at epoch 40.
That uniformity is why the driver can be a `for` loop and why a Slurm array over
the child directories needs no Python glue. And a sweep is pausable at any
granularity the project likes, including "stop after this child, I need the GPU",
with no cooperation from the sweep engine.

A second writer is deliberately avoided: nothing but the child writes the child's
manifest, so the index and the states cannot disagree. This also settles run
discovery for sweeps — the index names its children and reporting never scans
directories, which is what keeps a half-deleted run from silently vanishing from a
report.

### D24 — Three axis kinds, one dimension each; no conflict policy

The sweep block is `gate` plus `axes` plus `covariant_axes`. Every axis contributes
exactly one dimension, whatever its kind, so the child count is always the product
of the declared lengths and never depends on how many *paths* an axis touches.

Three kinds, and the split is a constraint rather than taste:

* an **ordinary axis** maps one dotted path to values;
* a **group axis** is an ordinary axis whose values name configuration-group
  options, so one value carries a whole file;
* a **covariant axis** maps a label to options, each assigning several paths at
  once.

Group and covariant axes overlap in effect — both set many fields per option — but
not in reach. **A group file's package path is static.** A group targeting
`model.heads.species.network` would need `# @package model.heads.species` baked in,
hardcoding a key the *configuration* chose, so it could never be reused for another
head. Everything below a configuration-keyed container is therefore reachable only
by a path or covariant axis. Conversely a group option is a committed, reusable,
diffable file, which a sweep-local mapping is not. Neither subsumes the other.

Two path checks, because each catches what the other cannot. The schema check
traverses a mapping wherever the next segment is a concrete key, which is what makes
`model.heads.species.network.hidden_dims` checkable at all; but `dict[str, Head]`
admits *any* key, so a misspelled head name passes it. The data check — is this
concrete key present in the composed configuration — catches that, and is a dict
lookup at preparation. Verified against the demo schema: a typo in the leaf fails
the first, a typo in the head name fails only the second.

**No `conflict_policy`.** The sibling design offered `default` (the axis wins) and
`strict` (refuse if the swept path is set anywhere else). Only `default` survives,
and it is behavior rather than a knob, because `strict` fails three ways: it is
incompatible with group axes, whose package path is *always* set by the base
defaults list; the misspelling it chiefly guarded is caught precisely by axis-path
validation; and its remedy pressures an author to delete a base default that is
meaningful when the configuration runs alone. What replaces it is information — one
warning per displaced axis, not per child. The same posture that retired `--force`:
describe what is happening rather than demand a flag.

An override contesting a swept path is refused rather than silently losing. It
would otherwise be a no-op, since the axis assigns that path per child regardless,
and a no-op that looks like an instruction is worse than an error.

To eliminate hand-rolled Hydra closure boilerplate (and prevent dropping non-sweep
overrides, `extra_dirs`, nested `a/b` group syntax, or the group-shadow check),
`plan_sweep` and `prepare_sweep` accept `compose_spec: ComposeSpec | None = None`
and `extra_dirs=()` directly, building both per-child composition and
`check_no_group_is_shadowed` automatically via exported `sweep_composers(...)`,
while keeping explicit `compose_child`/`compose_without` callbacks as escape
hatches. Empty axis lists `[]`, empty nested mappings, and empty covariant options
`{}` are refused before expansion, whereas single-value ordinary axes
(`sweep.axes.epochs=[10]`) remain permitted for temporarily pinning an axis via
CLI overrides.

### D25 — The project declares what completion means; Honbu reserves less

Four changes with one root cause. `open_run` recorded `finished` unconditionally
on normal return, so **"the work function returned" meant "the run is complete."**
That single conflation produced a missing capability and a live bug, and forced a
reserved flag to exist as a special case.

**Completion is declared, not assumed.** A producing command names the steps that
constitute a complete run, defaulting to every step it declares. On normal return,
all of them recorded complete gives `completed`; anything less gives **`partial`**,
a sixth state that joins the resumable set. A project with an optional `evaluate`
step narrows the set so an unrun evaluation does not hold the run open.

**A command declares whether running it is work on the run.** The question is not
"what state does this command set" but whether its execution advances the run at
all. A command that does not advance it records its own timeline events, and the
run's state is left exactly where it was found.

That covers three shapes with one declaration. An augmenting command never
advances a run — which fixes a real defect, not a refinement: `open_run` recorded
`started` and `finished` for augmenting commands too, so evaluating a finished run
flipped it to `running` and back, and a *failed* evaluation recorded `failed`,
erasing that training had completed. A **preparing** command — create the
directory now, work it in tonight's allocation — does not advance it either, so it
declares no steps and leaves the run `initialized`, which is what the manifest
already held. And an ordinary working command advances it, with completion derived
from its declared steps.

This is deliberately a boolean rather than a nameable target state. States are
derived from the last reserved timeline event and never written independently,
which is what keeps the state and the timeline from disagreeing; letting a command
*name* a state would either break that or require an event per nameable state. It
would also let a command that ran nothing declare itself `completed`, reintroducing
the exact lie this decision removes. A boolean cannot express that.

The narrowing is worth stating: `partial` now means only *a working command
returned with required steps outstanding*, and no longer doubles as "a command that
never intended to work this run." An earlier draft of this decision forced a
preparing command to declare the working command's steps just to land on `partial`,
and needed a spec rule saying so — a symptom whose cause this removes.

**`--init` is removed** (and Honbu owns no reserved CLI flags; see D19). `--init`
was the only flag that decided *how much project work happens*, rather than acting
before or around project code. With `partial` and `advances_run=False`, a project
declares its own `prepare` command whose handler opens the run and runs no steps,
naming it whatever it likes. `initialize_run()` survives as a function, because
sweep preparation genuinely needs it (D23). Removing `--init` also frees the word
`init` for the near-universal "scaffold a project here" meaning.

**Source applicability lives on `CommandSpec`; CLI option surfaces belong to the
project.** `CommandSpec` still declares which `config_sources`, `target`, and
`scope` apply semantically, and `honbu.cli.select_config_source` enforces accepted
sources at call time. Which CLI flags a command exposes and how its help is
rendered belong to the project's CLI library (optionally importing `honbu.cli.presets`),
removing the parallel parser-vs-help drift problem at the root (D19).

### D26 — Comparison is levelled, validates schema defaults, and evaluates pure hashes

Comparing two configurations is not one question, it is four, and the framework
already has the vocabulary for all of them: `literal` compares raw dicts as
written, `authored` validates through `root_type` (normalizing schema defaults)
and strips resolved-only fields, `behavioral` strips everything the markers
exclude from behavioral sources, and `by_hash` evaluates declared hashes and
attributes differences.

Validating operands through `root_type` at `authored`, `behavioral`, and `by_hash`
is load-bearing: `resolved.yaml` materializes every schema default while an
authored file omits defaulted keys. Without validation, comparing an authored file
against the run produced from it reports dozens of false `<absent> vs default`
differences; `literal` stays raw so callers still have a true as-written diff
(and so group fragments remain comparable at `literal`).

Prior-run operands use four clean sources — `default`, `file`, `composed`
(`config/composed.yaml`), and `resolved` (`config/resolved.yaml`, accepting either
a file or a run directory, eliminating the redundant `run` source) — with optional
per-operand `overrides`, `extra_dirs`, and `config_index`.

`behavioral` is the valuable baseline and is nearly free — it is exactly the
projection a digest already sees (`excluded_source_paths()`). It also defines a
**residual difference**: one that appears at `literal` and vanishes at
`behavioral`. Run comparison (`compare_runs`) shares this exact implementation.

Availability follows from inputs — bare mappings get `literal`, a root type unlocks
`authored` and `behavioral`, a hash registry unlocks `by_hash` — and an unavailable
level is refused rather than downgraded.

**`by_hash` evaluates every pure (`reads_inputs=False`) hash.** An earlier draft
attributed only `FieldHash` statically and marked every `SourceHash` and
`FannedHash` opaque. Because D5 explicitly observes that almost every domain hash
in `dojo` and `sati` (`target_schema_hash`, `model_config_hash`,
`preprocessing_hash`, `head_hashes`) is a `SourceHash` or `FannedHash` with
`reads_inputs=False`, static-only attribution left 5 of 6 hashes opaque. Calling
`hashes.compute(..., input_reading=False)` on the validated operands gives exact
digests and per-hash source diffs for every pure hash (refusing missing `command`
when a registry carries command-scoped `FieldHash` declarations), while reserving
**opaque** honestly for `reads_inputs=True` hashes (`dataset_hash`, `ByteHash`)
whose external disk/network inputs cannot be read at config-compare time.

### D27 — Materialization is configs-only, closure-driven, and checked by two drift views

Packaged defaults already work on the read side: `ComposeSpec.packaged` composes
from an installed package and the local directory shadows it per group. What was
missing is the write side — getting that content into a working tree so it can be
edited.

**Configs only.** Materialization covers packaged configuration trees and nothing
else (no fixture datasets or binary data assets). Projects wanting starter data
link to online datasets or handle dataset setup in their own CLI beside Honbu's
config materialization, keeping one packaged tree mapping cleanly onto one local
config directory.

The mechanism is a closure over declared roots, and the closure comes from the
compositor's **own record of what it selected** (`hydra.runtime.choices`), not
from re-parsing defaults lists. Verified: composing with the Hydra config returned
gives the exact transitive group→option map, with `hydra/*` internal groups
filtered out and `@package` suffixes stripped (`group@pkg` → `group`) so
package-relocated groups map to their real packaged file paths. Roots may be
packaged configuration names (with optional overrides) or an external authored
file (materializing only its packaged dependencies while leaving the authored file
untouched). Re-implementing defaults-list resolution would be a second copy of the
selection rules, and its drift would be silent: the copied tree would compose
differently from the packaged one it was copied from.

Scope *names* are the project's — Honbu ships none, exactly as it ships no step
names. Each `MaterializationScope` cleanly separates closure `roots`, optional
verbatim config-tree `files` (for non-YAML config files like TOML styles or
copying a whole directory), and optional `exclude` globs; directory walks always
skip Python package plumbing (`__init__.py`, `__pycache__/`, `*.pyc`, `*.pyo`). A
unified `plan_materialization` derives the packaged root from `ComposeSpec`
automatically (with an optional override for unpacked test trees) and returns a
`MaterializationPlan` exposing `.created`, `.skipped`, `.overwritten`, and
`render_plan()`.

Materialization never deletes. Deciding a local file is obsolete requires knowing
whether a human edited it, which the framework cannot know — so it adds, and
divergence is answered without a lockfile by **two complementary drift views**:

* **Composed drift (`compare_materialized_config`)** composes packaged-only (via
  explicit `ComposeSpec.local = None`, eliminating any sentinel-directory hack)
  against local-shadowing-packaged, accepts `overrides` for non-default groups or
  experiments, and reuses `honbu.compare.compare_configs` (D26). Because the local
  side still falls through to packaged defaults, partial local trees compare
  cleanly and answer *"ignoring paths, comments, and templates, does my local
  config actually change behavior or move a hash?"*
* **File-level drift (`materialized_file_drift` + `render_file_drift`)** scans the
  local tree against packaged defaults (respecting plumbing and optional
  exclusions) and classifies paths as `modified`, `unchanged`, `local_only`, or
  `packaged_only` — answering *"after an upgrade or local edits, which files in
  `./configs/` differ?"*

No command name is reserved (D19, D25): `honbu.materialize` exposes pure Python
functions, and `honbu.cli.presets` MAY offer an optional Typer command builder
mountable under whatever name a project chooses.

### D28 — A directive is authored and excluded; the sweep block is the first one

The three existing exclusions from behavioral sources are all claims about
*provenance*: a value Honbu generated, a filesystem path, a template rendered in
place. The sweep block fits none of them. It is authored by hand, it is not a path,
and it is not a template. But it is emphatically not an input to the work: it
describes how a *set* of runs is generated, and every run it generates computes the
same thing whether or not it is there.

So the fourth marker is a claim about **audience** rather than origin — an authored
instruction to the framework that the work does not read. Stretching the derived
marker to cover it would have been cheaper and would have asserted something false,
which the walkers would then propagate everywhere.

This is not tidiness. The spec promises that a prepared child's digest equals that
of the same configuration invoked by hand, and the obvious mechanism — preparation
strips the block from each child — does not deliver it. A base configuration may
legitimately carry a block during an ordinary run, most naturally a default `gate`
so that a sweep declared entirely in overrides inherits one. Run that configuration
by hand and it has a `sweep` key; run it as a child and it does not. If any digest
reads the block the two disagree, and nothing announces it: both runs succeed, and
only the identities are wrong.

That it does not bite today is an accident of taste. `FieldHash.include` is
mandatory, so a project names blocks explicitly and no project has yet named this
one. The first broad include set breaks the guarantee silently — precisely the
failure the marker system exists to make impossible, and precisely what the hashing
module already says of itself: marker exclusions are applied after the include set
so that a newly marked field cannot be readmitted by an include set nobody
revisited.

Two consequences follow. The block is **not** a requirable block, because sweeping
is not universal and the four requirable ones are the only structures both sibling
projects genuinely share; a project that never sweeps declares nothing. And because
it is optional, the framework verifies the declaration at the moment a sweep is
actually asked for — both that the field exists and that it is marked. The marker
is checked rather than trusted for the reason above: forgetting it is invisible.

### D29 — A sweep is a directory you add to, not a definition you re-declare

A sweep's identity comes from its **directory**. The definition digest is a
separate value that changes freely.

The first implementation collapsed them — the sweep identity was a slug of the
definition digest — and the consequence only showed up in a test: adding one value
to an axis renamed the sweep, so under a child template naming it, every child
resolved somewhere new and the eleven that had already run were orphaned. That is
exactly the situation re-preparation exists to handle, and it was the one situation
it could not.

The glossary had already drawn the distinction and the implementation ignored it:
`sweep_id` is "an identity for one sweep, assigned, never authored"; `sweep_hash`
is "a digest over the sweep's definition". Two values, two questions — *which sweep
is this* and *what does it currently describe*. Only the second is allowed to move.

The right anchor is the one runs already use. A run is its directory; an augmenting
command adds an evaluation to it without forking it. A sweep is its directory; a
grown definition adds children to it without renaming it. The index accumulates the
way a manifest accumulates invocations, and each entry stays immutable once
written.

That leaves shrinking, which is not symmetric with growing. A removed axis value
does not remove a child, because a child that ran holds results and its directory
does not vanish when the definition stops mentioning it. Dropping the entry would
leave that directory on disk and unreferenced — the silent omission the index
exists to prevent, arrived at from the other direction. So the entry is retained
and marked as outside the current definition, and restoring the value adopts the
child back rather than preparing a second one.

The identity is derived from the path on first write and then persisted, which
costs nothing and buys two things: a first preparation is reproducible without
consulting any state, and a directory that is later moved keeps its identity rather
than quietly becoming a different sweep.

### D30 — Sweeps split at declaration, axes, and planning

`honbu.sweeps` is a public operation area, not a single implementation module.
Its declaration types and root-contract helpers live in `definitions`; axis
normalization, classification, and cartesian expansion live in `axes`; the
cross-axis policy and `SweepPlan` construction live in `plan`. Gate checks,
preparation, and progress/execution remain their own modules. The package
re-exports the intended public API so callers import `honbu.sweeps` rather than
depending on the internal split.

This separates stable declaration vocabulary from the policy that turns it into a
concrete matrix, without prematurely splitting every individual check into a
module. Future execution adapters belong under `honbu.sweeps` because they enact
one prepared sweep rather than defining a framework-wide integration surface.

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
- How far the inspect/reporting scaffolding is shared vs. left per-project.
- Run discovery for *ad-hoc* comparison: comparing an arbitrary set of runs still
  has no definition of how they are located. D23 answers it for sweeps — the sweep
  index names its children, and nothing scans directories — but a user comparing
  two unrelated runs has only "a set of paths".
- Content-addressed output — naming a directory after a digest of what you are
  about to write — needs stage-then-commit, i.e. two targets for one command.
  Deliberately out of scope; a command has one target.

Answered during review, retained here for the record: the **metrics contract** is
a flat `best.metrics` summary in the manifest, with the project's own substrate
left untouched (D14); the **work-function seam** is `work(res) -> None`, with
artifacts registered progressively and completion signalled by returning without
raising (D13, D14).
