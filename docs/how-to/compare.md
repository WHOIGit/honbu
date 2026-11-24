# Compare configurations and runs

Two configurations or runs disagreed, or you want to rank a sweep or check whether
two runs are compatible to ensemble. `honbu.compare` covers both configuration
comparison and manifest-backed run comparison.

## Compare configurations

```python
from honbu.compare import ConfigOperand, compare_operands, render_comparison

comparison = compare_operands(
    [
        ConfigOperand(source="resolved", reference="runs/reef/dynamic-macaw"),
        ConfigOperand(source="resolved", reference="runs/reef/polite-cricket"),
    ],
    project=PROJECT,          # or compose_spec=CONFIG, root_type=RootConfig, hashes=HASHES
    level="behavioral",
)
print(render_comparison(comparison))
```

An operand names any of the four sources — `default`, `file`, `composed` (a run's
authored `config/composed.yaml`), or `resolved` (a run's `config/resolved.yaml`) —
optionally with its own `overrides`, `extra_dirs`, and `config_index`. Comparison is
N-way, so three runs and a baseline is one call.

### Choose the level deliberately

| Level | Compares | Answers |
| --- | --- | --- |
| `literal` | every key as written (raw, unvalidated) | "what differs in these files?" |
| `authored` | schema-validated authored values, ignoring resolution | "did we ask for different things?" |
| `behavioral` | schema-validated digest projection — markers excluded | "are these the same experiment?" |
| `by_hash` | all `reads_inputs=False` declared digests + per-hash diffs | "which hashes match or diverge?" |

`behavioral` is usually the one you want. It normalizes schema defaults, ignores
differences that cannot change a result — the run's own name, machine paths,
rendered identities, directives — and shows the ones that can. At `by_hash`, every
pure (`reads_inputs=False`) declaration (`FieldHash`, `SourceHash`, `FannedHash`) is
evaluated directly; only `reads_inputs=True` hashes are reported in `opaque_hashes`.

## Compare runs and sweeps

`compare_runs` combines manifest-backed compatibility gating, N-way configuration
comparison, and objective ranking over each run's `best.metrics`:

```python
from honbu.compare import Objective, compare_runs, compare_sweep, render_run_comparison

report = compare_runs(
    [run_a, run_b, run_c],
    gates=("dataset_hash",),
    objective=Objective(metric="mse", direction="min"),
    project=PROJECT,
    config_level="behavioral",
)
print(render_run_comparison(report))

# Or roll up every active child of a sweep directory in one call
sweep_report = compare_sweep(
    sweep_dir,
    gates=("dataset_hash",),
    objective=Objective(metric="mse", direction="min"),
    project=PROJECT,
)
```

Runs that have not recorded the requested objective metric (for example `initialized`
or `failed` runs) appear cleanly in `report.unranked` with their state and reason
rather than crashing the comparison.

## Gate compatibility across runs

Before ensembling checkpoints, merging predictions, or claiming two results are
comparable, call `check_run_compatibility`:

```python
from honbu.compare import check_run_compatibility

compat = check_run_compatibility(
    [run_a, run_b],
    gates=("dataset_hash", "split_hash"),
    on_unreconcilable="warn",   # default; pass "refuse" to raise RefusalError
)
```

When two runs recorded the same hash under different `encoder_version` or recipe
`version`, Honbu walks a version-aware reconciliation ladder:

1. Direct digest comparison when versions match.
2. Exact byte-digest comparison for `ByteHash` artifacts.
3. Re-hashing stored sources under the current canonical encoder when only
   `encoder_version` changed.
4. Comparing stored `config/resolved.yaml` projections for `FieldHash` entries.
5. Key-compatible comparison of stored domain sources (ignoring `"version"`).
6. Reporting `unreconcilable` (warning by default, or refusing when
   `on_unreconcilable="refuse"`).

## Mount as a Typer command

Honbu does not reserve a `compare` command name, because useful flags are
project-shaped. If you want a ready-made Typer config-diff command, mount
`honbu.cli.presets.build_compare_command`:

```python
from honbu.cli.presets import build_compare_command

APP.command("compare")(build_compare_command(project=PROJECT, default_level="behavioral"))
```
