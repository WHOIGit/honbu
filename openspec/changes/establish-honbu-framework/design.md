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

Three layers, each opt-in by *import* — see D20, which flattened the install:

```
  L3  CAPABILITIES   sweeps · run-comparison · ensemble-assessment ·
                     inspect scaffolding
  L2  FLOW           compose→resolve→collide→persist→work→complete ·
                     CLI dash-grammar · provenance bundle · run manifest
  L1  MECHANICS      canonical JSON · hash_source · IDs · strict-model base ·
                     markers + walkers · atomic writes · template rendering ·
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

**The numbering is a dependency ordering, not a stack of altitudes.** Composition
and identity, in particular, are *siblings*: neither imports the other, and the
first thing needing both is resolution. A reader who takes L2 as "above" L1
conceptually will look for a coupling that is not there.

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

The guard checks *presence and base type only*. See D3 for why it stops there.

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
wrapper), serialize through one shared canonical-JSON path, and carry a `version`
field; the configuration identity carries no version token (its identity is its
content). Portable-path and generated/derived-identity fields are excluded by
walking type markers — through mapping values and sequence elements as well as
direct submodels — never a hand-maintained list. Deterministic human IDs derive
from a hash, domain-seeded. This is L1: identical in both projects by
construction, so the class of drift that motivated Honbu cannot recur.

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

Each hash is a *declaration* in one of three forms: selected configuration fields
(the drift-prone case, so it is declarative data — an include set, optionally
per-command, plus per-hash excludes), a project-built source that carries its own
recipe version (computed values, versioned constants, data-derived listings), or a
digest over file bytes. Field selection alone covers `config_hash` and almost
nothing else — every domain hash in both projects synthesizes or normalizes
something — so the callable form is not an escape hatch, it is the common case.

What a hash does **not** carry is which operations it gates. An earlier draft put
`{role, gates}` on each hash and had the engine walk a run's recorded hash block.
That is the wrong home: it freezes a *policy* into a historical artifact, so
changing your mind about what an ensemble requires would mean rewriting old runs.
Instead the requesting operation names the hashes it gates, resolved against the
live registry, and a consumer ignores hash names it does not recognize. The
framework still needs no domain knowledge; it just asks the operation instead of
the artifact.

A hash whose external inputs are absent is **deferred**, not missing and not an
error — which is what makes an optional byte-reading identity (scan the
materialized dataset) a normal absence rather than a degraded run, and what lets
read-only inspection resolve everything else.

### D6 — One engine for sweeps and comparison; a clean work-fn seam

A sweep declares a base config, axes (config path + values), a strategy, an
output template, and an objective (a metric key + direction). The framework
expands the axes, composes/resolves each child, assigns child identities, writes a
sweep index, and — as children complete — collects canonical metrics and groups by
configuration identity, without knowing what the work function trains.

Amended by D22 and D23: expansion and execution are two commands, not one call.
Preparation resolves and materializes every child but runs nothing; execution is a
resumable loop over the index. So the framework does not "run each through the
lifecycle" in one pass — a sweep that could only be run to completion in a single
process is the thing D23 exists to avoid. N-way config comparison and a
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
explicit deferral token that survives serialization and appears in behavioral
sources, so a run that deferred and a run that pinned the same value are visibly
different runs with different digests. The alternative — absence as meaning — was
already in use and already cost a project a knob it could no longer turn off,
because the sentinel consumed the only spare value.

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
one. Both result sets coexist in one run directory, each with its own recorded
identity.

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
where `interrupted` and `failed` are discriminated by *who stopped it*, an external
signal versus an internal exception. Resumability is not the discriminator: an OOM
crash is often resumable too. `state` is a materialized view, always derived from
the last reserved timeline event and never written independently, so it cannot
drift; the timeline itself is an open append-only log the project can add domain
events to.

A run directory accumulates invocations — augmenting commands are invocations —
so the manifest carries an `invocations` array and timeline events index into it.
`cli.txt` is dropped: two files that must agree is the one-writer problem again.
Each invocation references a config index, deduplicated on the canonical bytes of
the resolved configuration rather than on `config_hash`, so two invocations
differing only in a hash-excluded field stay distinguishable. Configs persist at
`config/` for the first and `config/<N>/` thereafter, keeping the obvious path
obvious.

### D15 — Extending a completed run produces a new run with recorded lineage

Continuing training from a finished run — same trajectory, more epochs — is
tempting to model in place. It is refused. A directory whose artifacts came from
one configuration for 100 epochs and another for 50 more has no honest
`config_hash`, and the truthful answer to "what produced this checkpoint" is a
sequence, which a single manifest cannot express. Both projects already spell
warm-starting as a first-class config concept (`weights.source = "checkpoint"`),
so the capability is not lost, only relocated.

`--continue-from <run_dir>` therefore reads that run's **composed** configuration
as the base, applies overrides, resolves a **new** directory, and records
`parent: {run_dir, config_hash, artifact}`.

The composed form rather than the resolved one, because resolution absolutizes
input paths: a resolved `./datasets/coco8` is stored as
`/home/you/datasets/coco8`, which is correct on the machine that produced it and
absent on the cluster node you meant to continue in. The authored form
re-absolutizes wherever it runs. The accepted cost is that it holds only what was
authored, so a schema default that moved since the parent ran is silently picked
up; the parent's recorded `config_hash` makes that detectable if a project cares
to check. Resolved-only fields are stripped as well, but that is now a safety net
for a project that authors into one rather than the mechanism.

This is the sharp line between the two sources. A replay keeps everything, so the
parent's literal output directory wins and the invocation lands in that same run.
A continuation must not inherit that directory, or two configurations' artifacts
end up in one place with an honest identity for neither.

Copying the parent's other files — metrics, results, the manifest — is deliberately
not done. It would make the child's records describe someone else's run: copied
metrics would span two configurations under one `config_hash`, and a copied
manifest would hand the child the parent's identities and timeline outright. The
lineage pointer already reaches the parent's files in place, and only the project
knows which of them are continuable state rather than a record of a different run. Whether the child restores full
training state or only loads weights is the project's business and shows up in the
child's own configuration; Honbu records the link and nothing about its semantics.
Lineage is wanted by L3 regardless.

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
of any of them: the default config name, an authored file, a prior run's resolved
configuration (a file or a run directory), and a parent run to continue from. A
command **declares which of them it accepts**, which is what lets an augmenting
command refuse a freshly composed root with a message that names the fix instead
of failing later at collision time, and lets a command that needs no configuration
at all declare none.

Replaying a resolved configuration would otherwise trip the both-set refusal on
`dir` and `dir_template`, since a resolved artifact carries both. It does not,
because that refusal exists to catch *authoring* ambiguity: under a replay `dir`
wins and the check is skipped. This is also why a replay lands in the original
directory while a composed configuration produces a fresh run — the literal `dir`
pins it, reusing a feature rather than adding a special case.

The argv split is Honbu's because Typer would reject `data.uri=x` outright. Tokens
containing `=` are overrides — which handles Hydra's own `+`, `++` and `~`
prefixes for free, as all are dash-free. Dash-prefixed tokens are options, and
every legal one is either a framework option or declared by the command, so their
arity is always known and a following bare token is never ambiguous. Remaining
bare tokens are positionals, matched against the command's declaration.
Positionals reach the command, never the configuration: two ways to set one field
is how the grammar starts leaking.

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
with a marker naming the hash:

```
  prepared_dataset_hash: Annotated[ResolvedString, FromHash("prepared_dataset_hash")] = None
```

rather than in a side table of dotted paths. There is no path to mistype, the
existing marker walkers already find such fields (containers included), and a
project subclass that adds one is picked up automatically. Only the hash *name*
remains a string, cross-checked against the hash registry at construction.

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
construction, where a marker can be checked to target only resolved-only fields.
Ordering between two hook-assigned identities is invisible, so a refactor that
swaps two lines yields a template rendering `None` into a directory name with
nothing raising. The import-time refusal of a target template naming a deferred
value degrades to a no-op, because nothing records that a field came from a hash.
And a single hook cannot span phases, so needing one identity before rendering and
another after grows the phase list by two undocumented entries. There are no hooks
anywhere in L2: the project already holds the loop and owns work, so there is no
gap for one to fill.

This yields a clean asymmetry worth stating outright: **output templates are
discovered by type** — anything typed `OutputBlock` or `OutputLeaf` carries the
directory pair, so phases 5 and 6 walk the tree and need no declaration — while
**identities are declared by marker**, because no type says "a hash goes here."

Two consequences to record rather than rediscover. Resolution is a function of the
composed configuration, the command, the invocation context *and*, for an
augmenting command, the existing run manifest it inherits its directory from — so
it is not a function of the configuration alone. And phase 7 revalidates the tree,
so every project `model_validator(mode="after")` runs a second time against a tree
that now has identities assigned and paths absolute; a validator asserting
something true only of an unresolved tree will pass composed and fail resolved.

### D19 — The harness declares once; the CLI library is a seam, not a dependency

The plan was a Typer harness. Building it surfaced a duplication problem: Honbu
already declares the command tree (name tuples), every option with its arity, and
every positional with its required flag — it *had* to, because the argv split runs
before any CLI library sees `argv`, and only declared arity makes a following bare
token unambiguous. Restating all of that in Typer decorators is a second source of
truth for the same surface, which is the drift pattern Honbu exists to remove.

So the threading — parse, compose, resolve, open the run, call work, exit — is a
plain function that knows about no CLI library. On top of it, Honbu ships its own
dispatcher: match the longest registered command path, split the remainder, and
generate help from the declarations. That is roughly a hundred and fifty lines and
buys a single source of truth. What it gives up is shell completion, which nothing
in either project uses today and which can be added against the same declarations
later.

A project that would rather keep Typer is not blocked: the per-command flow is
callable with the remaining tokens, so a Typer callback with
`allow_extra_args=True` is a two-line adapter. `typer` therefore leaves the
dependency list and `rich` stays, used for help formatting when present with a
plain fallback when not.

One consequence worth stating: a refusal and a defect exit differently. A
`RefusalError` is a message to the user, so it prints and exits non-zero with no
traceback. Anything else is a bug in Honbu or the project, so its traceback
survives — swallowing it would trade a diagnosable crash for a mystery.

### D20 — One install; the layers are import boundaries, not extras

Hydra, OmegaConf and PyYAML move into the core dependencies. `pip install honbu`
gives every layer, and a project opts out of a capability by not importing it.

The extras were justified by dependency weight, and that justification was real:
`hydra-core` → `omegaconf` → `antlr4-python3-runtime==4.9.*`, an exact pin on a
2021 release that every consumer now inherits. **This is the accepted cost**, and
it is the one thing that could force the decision back: a project needing antlr
4.10+ for something else has no resolution. Accepted because the alternative is
worse in a way that shows up on everyone's first command rather than in one
environment's dependency solve — three lazy-import branches that exist only to
say "install the flow extra", firing during someone's first invocation, plus an
extras matrix (`[flow]` vs `[compose]` vs `[run]`) presented at adoption time to
exactly the person least equipped to choose.

Two things this gives up, one of which is bought back:

*Structural enforcement of the layering.* It was previously **impossible** for
`mechanics/` to import a composition primitive, because the package was not
installed. That is now convention. It is bought back with a test asserting that no
module under `mechanics/` imports hydra, omegaconf or yaml — cheap, and treated as
non-optional, because "two modules that should be independent quietly grow a
dependency" is the drift Honbu exists to remove.

*The zero-dependency mechanics case.* A project wanting only the canonical encoder
and the markers now installs antlr to get them. Not bought back; judged not worth
an extras matrix.

Genuine launchers stay optional, because they are not capabilities Honbu
implements: `submitit`, `optuna` and anything Slurm-shaped are extras. See D22 for
why they are used as libraries rather than as Hydra plugins.

### D21 — The configuration identity is optional and project-bound

`runtime.config_hash` carries no framework-supplied hash binding, and
`runtime.config_id` defaults to null. A project that wants a hash-derived
configuration identity declares the hash and binds the field; a project that wants
none omits both, and neither the run lifecycle nor the manifest requires them.

This reverses a decision taken and reverted inside one review pass, which is worth
recording rather than quietly overwriting. The base block briefly bound
`config_hash` to a reserved hash name, because `config_id` defaulted to a template
over it and that default was otherwise unusable — it silently minted entropy until
the null-token refusal exposed it. Binding fixed the default but made declaring a
hash *mandatory*, which contradicts a requirement written twenty lines earlier:
"the framework SHALL NOT infer identities from hashes: not every hash warrants an
identity, and not every identity derives from a hash." Defaulting `config_id` to
null resolves both — the shipped block has no identity behavior and no landmine.

The cost is real and is accepted as documentation rather than mechanism. Without
`config_id`, a project's first output template reaches for `runtime.run_id` or
`{:timestamp}`, both fresh per invocation, and a run directory named from fresh
entropy cannot be re-found by a later `--resume`. Every project therefore wants a
configuration-derived component in its run directory, and that is boilerplate the
adoption guide leads with rather than something the framework enforces.

`config_hash` remains the *conventional* name, and comparison takes the identity
name as a parameter rather than assuming it.

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
survives the driver process dying, which the plugin's version does not.

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
combination, check the gate, then write each child's run directory in the
`initialized` state without running anything. Execution is then a loop over the
index, and it needs no new vocabulary because Honbu's existing resumable set is
already exactly right: `initialized`, `running`, `interrupted` and `failed` are
resumed, `completed` is skipped. `running` being resumable is what covers a Slurm
timeout, where SIGTERM does not raise and the child is left mid-flight.

Two consequences worth stating. Every child is reachable by
`--resolved <child> --resume` — one uniform call, whether it never started or died
at epoch 40 — which is why the driver can be a `for` loop and why a Slurm array
over the child directories needs no Python glue at all. And a sweep is pausable at
any granularity the project likes, including "stop after this child, I need the
GPU", with no cooperation from the sweep engine.

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

**`--init` is removed.** It was the only reserved flag that decided *how much
project work happens*, rather than acting before or around project code — the
other six settle which configuration is used and whether Honbu may write where it
wants, all before any project code runs. That is the line, and `--init` was its
only crossing. With `partial`, the capability needs no flag at all: a project
declares its own `prepare` command whose handler opens the run and runs no steps,
naming it whatever it likes. `initialize_run()` survives as a function, because
sweep preparation genuinely needs it (D23) and that is Honbu's own code rather than
a user-facing surface. Removing the flag also frees the word `init` for the
near-universal "scaffold a project here" meaning.

**Flag applicability is derived, never registered.** The behavior flags were
accepted on every command and filtered only in help, so help and the parser
disagreed: `inspect config` advertised neither `--clobber` nor `--resume` while the
parser silently accepted both, and a typo produced silence instead of an error.
Every one of them is already implied by an existing declaration — `--config-dir`
applies where the command composes, `--clobber` where it can clear something,
`--resume` where it produces into a target — so a second declaration would only
create something to drift from the first. Derivation also makes help a description
of behavior rather than a parallel opinion about it. This is D12's move again: the
declaration supplies the radius, the flag supplies only permission.

### D26 — Comparison is levelled, and the levels are the markers

Comparing two configurations is not one question, it is four, and the framework
already has the vocabulary for all of them: `literal` compares everything,
`authored` strips resolved-only fields, `behavioral` strips everything the markers
exclude from behavioral sources, and `by_hash` attributes each difference to the
declared hashes it would move.

`behavioral` is the valuable one and it is nearly free — it is exactly the
projection a digest already sees, so it is `excluded_source_paths()` and nothing
new. It also gives a definition to a term the sweep spec already used without one:
a **residual difference** is one that appears at `literal` and vanishes at
`behavioral`. Run comparison is therefore this operation with runs as operands, and
must share the implementation rather than growing a second diff.

Availability follows from inputs — bare mappings get `literal`, a root type unlocks
`authored` and `behavioral`, a hash registry unlocks `by_hash` — and an unavailable
level is refused rather than downgraded. A comparison that quietly answered a
weaker question than the one asked would be worse than none.

The one honest limit is `by_hash`. Attribution works by asking whether a differing
path falls inside a field-selected hash's include set, which is decidable; verified
against the demo, `model.backbone` moves `config_hash`, `training_outputs.dir_template`
moves nothing declared, and `runtime.run_id` is marker-excluded from every digest.
But a hash whose source a *callable* builds cannot be attributed statically, and an
input-reading one cannot be run at comparison time at all. Those are reported
**opaque** — neither implicated nor cleared. Omitting them would imply a
completeness the analysis lacks, and "moves no identity" versus "moves no identity
I can see" support different decisions.

### D27 — Materialization takes its closure from the compositor, not from a parser

Packaged defaults already work on the read side: `ComposeSpec.packaged` composes
from an installed package and the local directory shadows it per group. What was
missing is the write side — getting that content into a working tree so it can be
edited.

The mechanism is a closure over declared roots, and the closure comes from the
compositor's **own record of what it selected** (`hydra.runtime.choices`), not from
re-parsing defaults lists. Verified: composing with the Hydra config returned gives
`{'heads': 'species_only', ...}` — the exact group→option map, transitive, with the
compositor's internal groups distinguishable by prefix. Re-implementing defaults-list
resolution would be a second copy of the selection rules, and its drift would be
silent in the worst way: the copied tree would compose differently from the packaged
tree it was copied from.

Scope *names* are the project's — `--minimal`, `--supervised`, `--ssl` are dojo's
vocabulary, and Honbu ships none, exactly as it ships no step names. What Honbu owns
is closure, planning, and the create/skip/overwrite discipline. Non-configuration
resources are materializable too, because a starter configuration pointing at a
fixture dataset is only useful if the dataset arrives with it.

Materialization never deletes. Deciding a local file is obsolete requires knowing
whether a human edited it, which the framework cannot know — so it adds, and
divergence is answered by *comparison* (D26) rather than by a lockfile. That is the
better answer anyway: it reports what differs rather than merely that something did.
It also finally answers a question the framework has been carrying unanswered, since
continuing a run reapplies schema defaults at continuation time and a default that
moved is otherwise picked up silently.

No command name is reserved, for the same reason no option governing how much work
runs is reserved (D25): reserving a name is the same imposition by another route.

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
