# Image classification

A classification project needs nothing that object detection does not. The shape is
the same and the differences are all in blocks Honbu never reads.

## What changes

**The blocks.** `dataset`, `network`, `optimizer`, `training` instead of `data`,
`model`, `training`. Honbu has no preferred names; the only structural requirement is
that the root stays flat.

**The digest's includes.** A `FieldHash` over `("experiment", "dataset", "network",
"optimizer", "training")`. What matters is that it covers what changes the
computation and nothing that changes only the accounting.

**The objective.** `run.record_best(epoch=e, metrics={"top1": 0.91, "top5": 0.99})`.
Record your framework's own metric names rather than tidied ones.

**Nothing else.** The commands, the run lifecycle, resume, provenance, sweeps and
comparison are identical, because none of them know what a class is.

## A sweep that suits it

Learning rate and batch size interact, so they are a natural covariant axis rather
than a grid:

```yaml
sweep:
  gate: runtime.config_id
  axes:
    network.depth: [18, 34, 50]
  covariant_axes:
    schedule:
      - {optimizer.learning_rate: 0.1,  training.batch: 256}
      - {optimizer.learning_rate: 0.05, training.batch: 128}
```

Three depths against two schedules is six children, not nine — see
[Sweep configurations](../how-to/sweeps.md).

## Where to start

The [tutorial](../tutorial.md) builds a complete project from nothing, and its
example — fitting a line — is deliberately closer to a classifier's shape than to a
detector's: one dataset, one objective, one checkpoint.
