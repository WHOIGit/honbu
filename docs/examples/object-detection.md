# Object detection

[**yolobu**](https://github.com/sbatchelder/yolobu) is a complete Honbu project
driving Ultralytics YOLO on real data and real GPUs. It is the pilot: everything in
these docs was validated against it before being written down.

## What it demonstrates

**A backend adapter that owns no data.** Source datasets stay where they are.
`prepare-dataset` validates a YOLO manifest in place and writes one normalized,
provenance-bearing manifest into a content-keyed cache. Editing the source YAML
produces a new cache entry and leaves the old one for the runs that used it.

**A framework contained under the run.** Ultralytics writes its own tree — weights,
plots, `results.csv` — into `backend/ultralytics/` inside the run directory, so a
foreign framework's output is part of the run rather than beside it.

**Producing and augmenting commands.** `train` creates the run and records the
checkpoint and best metrics. `eval validation` and `eval test` are augmenting
commands, each scoped to its own output leaf, so re-running the test evaluation
cannot disturb the validation results.

**Sweeps end to end.** A sweep definition is a named configuration group, selected
with `sweep=example`. `train` reads the composed configuration and prepares the
children it describes; there is no flag. Children live under `./runs/` with every
other run, gated on `runtime.config_id`, and the sweep itself lives beside them under
`./sweeps/`, holding the definition, the index, the progress cache and the results.

**An aggregate the framework does not define.** After execution, yolobu reads each
completed child's `results.csv`, selects the epoch that maximized
`metrics/mAP50-95(B)`, and writes a ranked table and a comparison figure. Honbu
supplies the seam — `record_best` and the child manifests — and no opinion about what
a good metric is.

## Worth reading for

- `src/yolobu/schema.py` — a real root: an extended sweep block, a templated identity,
  portable paths, and a `FieldHash` over exactly what decides behavior
- `src/yolobu/adapters.py` — one validator serving two callers, which is the
  copy-adaptation drift Honbu exists to remove, caught one level down
- `src/yolobu/sweeps.py` — what a project owns above Honbu's sweep machinery: where
  sweeps live, how a child is invoked, and what "the result" is
