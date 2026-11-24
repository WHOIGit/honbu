# Runs

A run is a directory. Everything that is true about it is inside it, and one writer
writes it.

```text
runs/linefit/dynamic-macaw/
├── manifest.json        # the live record
├── config/
│   ├── composed.yaml    # authored, portable
│   ├── resolved.yaml    # resolved, absolute
│   └── hashes/          # one artifact per digest
├── checkpoints/         # yours
└── reports/             # yours
```

## The manifest is the record

`manifest.json` holds the state, an append-only timeline, every invocation with its
argv and which configuration it used, the digests, the artifacts you registered,
progress, best metrics, and lineage.

It is written by the run and by nothing else. That is a deliberate constraint rather
than an implementation accident: the moment a second writer exists — a sweep driver
keeping its own tally, say — the two can disagree, and then a person has to decide
which to believe. With one writer there is no disagreement to adjudicate, and a
sweep's state is simply the aggregate of its children's manifests.

## Six states, derived not set

| State | Means |
| --- | --- |
| `initialized` | the directory and its provenance exist; no work has run |
| `running` | a step is in progress |
| `partial` | work stopped cleanly with steps outstanding |
| `completed` | every declared step finished |
| `interrupted` | a signal arrived |
| `failed` | an exception escaped |

The state is *derived* from the last reserved event on the timeline, never assigned.
A field that is set can be set wrongly; a value read off an append-only log cannot
disagree with the log. Everything except `completed` is resumable.

## Steps make progress durable

```python
if not run.skip("fit"):
    with run.step("fit"):
        ...
```

A step is the unit of "do not do this again". Entering one records that it started;
leaving it records that it finished; dying inside it leaves it unfinished, so the
next invocation with `--resume` runs it again and skips the ones that completed.

Declare a step when repeating it would be expensive or destructive. Do not declare
steps for everything — a step boundary is a promise that the work either happened or
did not, and fine-grained steps make that promise harder to keep than the repetition
costs.

## Producing and augmenting commands

A **producing** command creates a run. It resolves a target, checks it is safe,
writes provenance, and advances the run's state.

An **augmenting** command points at a run that already exists and adds to it. It
declares a narrow `scope` — the output leaves it is permitted to replace — and
usually does not advance the run's state.

```python
--8<-- "examples/linefit/project.py:commands"
```

The scope is what makes `--clobber` safe to have. `--clobber` used to mean "clear
the run directory" in one project and "rewrite the figures" in another, and no flag
can span blast radii that differ by three orders of magnitude. So the *declaration*
supplies the radius and the flag supplies only permission: `report --clobber` can
replace the report and provably cannot touch the checkpoints.

## Resume holds no state outside the run

```bash
yourproject train --resolved runs/linefit/dynamic-macaw --resume
```

That command is the same whatever happened: a run that never started, one that died
at epoch forty, and one killed by a scheduler timeout are all resumed by it. There
is nothing to decide per run, which is why a driver over many runs can be a `for`
loop and why a sweep needs no bookkeeping of its own.

## Recording results

```python
run.record_artifact("checkpoint", checkpoint)      # a file that matters later
run.record_progress(epoch=12, loss=0.31)           # where it has got to
run.record_best(epoch=50, metrics={"mse": 0.004})  # the objective
```

Honbu stores all three and **reads none of them**. It has no opinion about what a
good metric is, whether higher is better, or what your objective means. What it
guarantees is that the value is recorded atomically, beside the configuration that
produced it, in a place a sweep or a comparison can read later without asking your
code anything.

That seam is what lets a sweep rank its children without knowing what they trained.

## Collision safety

Before a producing run writes anything, its target is checked: is it inside the
declared output root, and is something already there?

An occupied target is described rather than blanked — the refusal says what is in
the way. Clearing is confined to the command's declared scope, and a target that
resolves outside its containment root is refused even if a symlink points there,
because the check resolves first and judges the destination.

---

Next: [Refusals](refusals.md) — the shape of Honbu's messages, and why there are so
many.
