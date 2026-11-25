# Honbu

Honbu — 本部, *headquarters* — is the shared foundation for configuration-driven
training projects. It assumes one shape of work:

<p style="text-align: center"><strong>configuration × fixed dataset → artifacts + metrics</strong></p>

and it owns everything around that arrow which every project otherwise rewrites:
composing configuration, deciding what a run *is*, placing it somewhere safe,
recording what produced it, resuming it when it dies, and expanding it into a sweep.

## What it is not

**Not a trainer.** Honbu contains no model, no optimizer, no data loader, and no
opinion about your framework. The [example project](tutorial.md) fits a line with
arithmetic; the pilot project drives Ultralytics YOLO. Neither is closer to Honbu's
heart than the other.

**Not a scheduler.** Local sequential execution needs nothing extra. Submitit and
Optuna are optional adapters, used as libraries.

**Never in control.** Your project holds the main loop and reaches into Honbu.
There is no framework callback you must implement and no base class you must
inherit. A project adopts the depth it wants and hand-rolls the rest — including
keeping its own CLI parser, if it has one it likes.

## Install

```bash
pip install "honbu @ git+https://github.com/WHOIGit/honbu.git"
```

Optional execution backends are available as `honbu[slurm]` (`submitit`) and
`honbu[optuna]` (`optuna`). One install carries every framework layer. A project opts
out of a layer by not importing it, not by choosing an extra:

| Layer | Import | What it gives you |
| --- | --- | --- |
| Core | `honbu`, `honbu.core` | The strict model base, base blocks (`HashedRuntimeConfig`), field markers, hash declarations, atomic writes, path portability, template rendering |
| Flow | `honbu.flow` | CLI-free declared commands, `ProjectSpec`, config loading, the fixed resolution order, collision-safe run directories, resume, the manifest |
| CLI | `honbu.cli`, `honbu.cli.presets` | Optional library-agnostic CLI helpers (`validate_override_tokens`, `split_dash_rule`, `exit_on_refusal`) and opt-in Typer option aliases and command builders |
| Operations | `honbu.sweeps`, `honbu.compare`, `honbu.materialize` | Sweeps, config/run comparison and compatibility gating, starter-tree materialization and drift |

The dependency direction is one-way and enforced by a test, so nothing below
reaches up.

## Where to go

<div class="grid cards" markdown>

- **New here** — the [tutorial](tutorial.md) takes an empty directory to a completed,
  resumable run in ten steps.
- **Doing a specific thing** — the [how-to guides](how-to/configure.md) are
  task-shaped: declare a hash, register a command, retype your paths, run a sweep.
- **Adopting an existing project** — start at [Adopt an existing
  project](how-to/adopt.md), which orders the work so each step stands alone.
- **Wondering why it refused you** — the [concepts](concepts/index.md) explain what
  Honbu is protecting, and [Refusals](concepts/refusals.md) explains the shape of
  its messages.
- **Looking something up** — the [reference](reference/markers.md) has the markers,
  the hash forms, the template grammar, the command-line surface, and the manifest.

</div>

!!! note "Every sample here is executed"

    The code in these pages is quoted from
    [`examples/linefit/project.py`](https://github.com/WHOIGit/honbu/blob/main/examples/linefit/project.py),
    a complete project the test suite runs end to end. If an API is renamed, that
    suite fails before these pages can mislead you.
