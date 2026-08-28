# Glossary

Terms used with precision across `design.md` and the capability specs. A word
defined here means only what it says here; where two projects used one word for
two things, that is recorded rather than resolved by fiat.

## Configuration and composition

**Composed configuration** — the plain mapping the compositor produces from the
authored files and overrides, before Honbu touches it. Persisted as
`config/composed.yaml`. It holds only what was authored; no assigned value appears
in it.

**Resolved configuration** — the composed configuration after the eight resolution
phases: input paths absolutized, digests assigned, template-valued fields rendered,
the target and its leaves resolved, revalidated. Persisted as `config/resolved.yaml`.

**Portable view / absolute view** — the two forms of one resolved configuration.
The portable view keeps bare-relative output paths relative and is what gets
persisted; the absolute view has every output path joined and is what work reads.
Their digests are equal, because paths never enter a digest.

**Configuration group** — a directory of interchangeable option files, one selected
per composition. Selection is an ordinary override (`data=nes-hf`), never a
distinct root source. A group file's package path is **static**, fixed by its
location and `@package` header.

**Config source** — one of the four mutually exclusive origins of a configuration:
the default config name, an authored file, a prior run's resolved configuration, or
a parent run to continue from.

**Replay** — reusing a prior run's *resolved* configuration, which carries a literal
output directory and therefore lands in that same run. Contrast **continue**, which
reuses a prior run's *composed* configuration and produces a new run with recorded
lineage.

## Identity

**Source** — the mapping a hash is taken over, projected into the configuration's
own flat key namespace and carrying a `version`. Persisted beside its digest so the
digest can be re-derived.

**Digest** — the bytes of a source through the frozen canonical encoder. Not an
identity: a digest is what was computed, an identity is what a project chose to
record.

**Identity** — a value recorded in the run manifest because a field declared it one.
Orthogonal to origin: not every hash warrants an identity, and not every identity
derives from a hash. A fresh `run_id` derives from nothing.

**Directive** — an authored field the work does not read: an instruction to the
framework about how runs are generated. Excluded from every digest by its marker,
like a path or a derived identity, but on different grounds — those are claims
about where a value came from, this one about who reads it. The sweep block is the
first (D28).

**Deferred hash** — a hash declared as reading inputs, and therefore always computed
after resolution rather than during it, whether or not its inputs happen to be
present. Declared, not discovered, so the deferred set is the same on every machine.

**Fanned hash** — one declaration yielding one entry per key of a mapping or element
of a sequence, addressed as `<name>.<key>`.

**`config_hash`** — the conventional name for a whole-configuration digest. A
convention, not a reservation: Honbu declares no hash and binds no field of its own
accord (D21).

**`config_id`** — a human-readable configuration identity, when a project declares
one. Defaults to null.

**`run_id`** — an identity for one run. Defaults to fresh per invocation, so two
runs of one configuration do not collide.

## Runs

**Run** — one directory produced by one producing command: a `manifest.json`, a
`config/` bundle, and whatever the work function wrote. The unit everything else
indexes.

**Producing command** — a command that creates a run directory. **Augmenting
command** — one that adds to an existing run.

**Step** — a named phase of a command's work, with its own write scope and a resume
policy of `continue` (its output is resume state) or `restart` (its output is
spoiled work).

**Scope** — the paths a command or step may *clear*. It governs clearing and
recording, never what a project is able to write; there is no filesystem sandbox.

**Containment root** — the configuration-named directory a destructive operation may
not escape. Every path Honbu clears is checked against it.

**State** — one of `initialized`, `running`, `partial`, `completed`, `interrupted`,
`failed`, derived from the last reserved event on the timeline and never written
independently. All but `completed` are resumable. `partial` means a command that
advances the run returned with required steps outstanding.

**Advancing command** — one whose execution counts as work on the run, so it records
the lifecycle events and reaches `completed` or `partial`. A non-advancing command —
a preparing one, or any augmenting one — records only its own invocation events and
leaves the state where it found it.

**Required steps** — the declared steps that must be satisfied for a run to be
complete, defaulting to all of them. A step is satisfied by being recorded complete
in any invocation, or by being declared not applicable with a reason.

**Invocation** — one command execution against a run. A run accumulates them; each
names the configuration index it ran under.

**Configuration index** — the identifier for one resolved configuration within a
run. Index 1 lives at the unsuffixed `config/`; later indices at `config/<N>/`.

## Comparison and materialization

**Operand** — one configuration entering a comparison, given as any of the sources
composition already accepts, plus a run directory. Every operand normalizes to a
composed mapping before comparison.

**Level** — which question a comparison asks. `literal` (every path), `authored`
(resolved-only fields removed), `behavioral` (everything the markers exclude from
behavioral sources removed), `by_hash` (differences attributed to declared hashes).

**Residual difference** — one that appears at `literal` and vanishes at
`behavioral`: the configurations differ, but not in a way any behavioral source
reads.

**Opaque hash** — one whose source a project callable builds, so a difference can be
neither attributed to it nor cleared of it statically. Reported as such rather than
omitted, because "moves no identity" and "moves no identity I can see" support
different decisions.

**Packaged defaults** — a configuration tree shipped inside the installed project
package, composed from when no local tree is present and shadowed per group by a
local one.

**Root** (materialization) — a starting point for a closure: a configuration name,
a group selection, or an authored file.

**Closure** — a root plus every packaged configuration it transitively selects,
taken from the compositor's own record of what it chose rather than by re-parsing
defaults lists.

**Scope** (materialization) — a project-named set of roots, and optionally packaged
non-configuration resources. Honbu ships no scope names.

**Plan** — the classification of every file a materialization would touch as
created, skipped, or overwritten. Reportable without writing.

## Sweeps

**Sweep** — a declared set of configurations expanded from one base configuration,
prepared as a set of child runs.

**Axis** — one dimension of the expansion. Declared as a target path mapped to a
list of values. Contributes its length to the cartesian product.

**Group axis** — an axis whose *values* name configuration-group options, so each
value selects a whole file, applied during composition. Cannot target a path below a
configuration-keyed container, because the group file's package path is static.

**Covariant axis** — an axis whose *options* are mappings of dotted path to value,
so several paths move together, applied after composition. One axis, one dimension,
however many paths each option assigns. Reaches anywhere a dotted path reaches,
including inside containers. Every option must assign the same set of paths.

**Child** — one run produced by one point of the expansion. Structurally an ordinary
run: its digests equal those of the same configuration invoked by hand, because the
sweep block is consumed at preparation and its membership is recorded in its
manifest rather than its configuration.

**Coordinate** — a child's position in the expansion: its index, plus the realized
axis values that produced it.

**Gate** — the configuration path a sweep declares as the value distinguishing its
children. Must be distinct across children, must depend only on the configuration
rather than the invocation, and must appear in the child output template.

**Sweep index** — the immutable record of prepared children written at preparation:
coordinates, gate values, realized values, run directories. Reporting locates
children through it and never scans directories.

**Preparation** — expanding the matrix, resolving every child, checking the gate,
and writing every child directory in the `initialized` state, running nothing.

**`sweep_id`** — an identity for one sweep. Assigned, never authored, and excluded
from every digest so membership cannot alter a child's identity. Derived from the
sweep's **directory**, not its definition, which is what lets a sweep be added to:
growing an axis extends the sweep rather than naming a new one.

**`sweep_hash`** — a digest over the sweep's definition — base configuration and
axes — under the standard exclusions, so two sweeps differing only in output
location share one. Unlike the identity, it is *meant* to change when the
definition does. Collapsing the two makes every edit a new sweep.

**Retired child** — one whose axis value the current definition no longer covers.
Kept in the index and marked, never deleted: it may hold results, and an index that
stopped mentioning it would leave its directory unreferenced.

## Words this project deliberately does not use

**Multirun** — the compositor's own sweep execution. Honbu reuses the compositor's
override *parser* and none of its multirun machinery, because multirun holds
progress in memory and cannot resume (D22).

**Results** — means unrelated things in the two sibling projects, so it is not a
framework block and carries no framework meaning (D2).

**Best** — a project's chosen result, stored verbatim in the manifest. Honbu never
selects it, ranks it, or reads the metric names.
