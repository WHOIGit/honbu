# Record an objective

Honbu stores your results and interprets none of them. There is no metric schema, no
direction convention, and no check that your objective improved. What it guarantees
is that what you record is written atomically, beside the configuration that produced
it, where something else can read it later without asking your code anything.

## The three recorders

```python
run.record_progress(epoch=12, loss=0.31)            # where it has got to
run.record_best(epoch=50, metrics={"mse": 0.004})   # the objective
run.record_artifact("checkpoint", checkpoint)       # a file that matters later
```

```python
--8<-- "examples/linefit/project.py:handler"
```

`record_progress`
:   Incrementally merges keys into `manifest["progress"]` as a run advances (use
    `run.set_progress(...)` to replace the mapping wholesale). It answers "is this
    still moving?" for a run in flight.

`record_best`
:   The result. Record it whenever the best changes, or once at the end — the
    manifest keeps the last thing you said.

`record_artifact`
:   Registers a file by name. A path inside the run is stored relative, so the run
    directory stays movable. `run.artifact("checkpoint")` reads it back, which is how
    an augmenting command finds what a producing one made.

## Why the shape matters

`best` is the seam every cross-run capability reads:

- a **sweep** ranks its children by reading each child's recorded best;
- a **comparison** puts the same metric from two runs side by side;
- a future **search** adapter reads its objective from exactly here.

None of them call your code. If `best` is empty, all of them have nothing to work
with — a sweep that trained perfectly well will aggregate nothing. This is worth a
test in your project:

```python
def test_training_records_a_best():
    ...
    assert Manifest.load(run_dir).data["best"]["metrics"]["mse"]
```

## Use your backend's own names

Record the metric names your framework produces, not names you invent to be tidy:

```python
run.record_best(metrics=dict(metrics.results_dict), epoch=epochs)
```

The pilot project records Ultralytics' `results_dict` verbatim, keys like
`metrics/mAP50-95(B)` and all. Renaming them would mean maintaining a translation
table — a second source of truth for something the backend already names — and the
first time the backend adds a metric, the table is silently incomplete.

## Best is not always last

If your objective peaks in the middle, the final validation is not the best epoch. A
run's recorded `best` is whatever you put there, so decide which question you are
answering: the *last* value is what the model in the checkpoint scores, the *best* is
what the run achieved. Recording both is fine — `metrics` is a free-form mapping.
