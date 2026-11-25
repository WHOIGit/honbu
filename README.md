# Honbu

**Honbu** (本部, *headquarters*) is a composable Python framework for reproducible ML experiments: strict Hydra + Pydantic configurations, deterministic behavioral hashing, and collision-safe run directories & provenance — and no more.

It adapts to your project's own inputs, outputs, and CLI while leaving training, models, data loading, and metrics entirely to you.

## What Honbu gives you

| Layer | Import | Responsibilities |
| --- | --- | --- |
| **Core** | `honbu`, `honbu.core` | `StrictModel`, `PortablePath`, `HashedRuntimeConfig`, `OutputBlock`/`OutputLeaf`, field markers, declarative hashes (`FieldHash`, `SourceHash`, `ByteHash`, `FannedHash`), pure templates, atomic writes |
| **Flow** | `honbu.flow` | `ProjectSpec`, `CommandSpec`, Hydra composition with search-path precedence, fixed-order pure resolution, collision-safe `open_run`, resumable steps, `--fork-from` lineage, single-writer `manifest.json` |
| **CLI** *(opt-in)* | `honbu.cli`, `honbu.cli.presets` | Library-agnostic helpers (`split_dash_rule`, `validate_override_tokens`, `resolve_positional_config`, `select_config_source`, `exit_on_refusal`) and optional Typer aliases & mountable command builders |
| **Operations** *(opt-in)* | `honbu.sweeps`, `honbu.compare`, `honbu.materialize` | Prepared grid/covariant sweeps, N-way config & run comparison, compatibility gating, and config-tree materialization & drift checks |

You keep the main loop — there is no `Trainer` base class, no mandatory CLI dispatcher, and no hidden multirun mode. Dependency direction between layers is strictly one-way and enforced by tests.

## Install

Requires Python 3.12+.

```bash
pip install "honbu @ git+https://github.com/WHOIGit/honbu.git"
```

Optional extras:

```bash
pip install "honbu[slurm] @ git+https://github.com/WHOIGit/honbu.git"   # submitit
pip install "honbu[optuna] @ git+https://github.com/WHOIGit/honbu.git"  # optuna
```

## Minimal shape

A Hydra config packaged at `myproj/config_defaults/config.yaml` (Honbu fills in `runtime.run_id`, `runtime.config_hash`, and `outputs.dir` during resolution):

```yaml
defaults:
  - _self_

experiment:
  name: baseline
runtime:
  seed: 42
output_root: ./runs
outputs:
  dir_template: "{experiment.name}/{runtime.run_id}"
epochs: 10
```

And the matching schema, spec, and run loop in Python:

```python
from pathlib import Path
from typing import Annotated
from pydantic import Field
from honbu import (
    DIRECTIVE, ExperimentConfig, FieldHash, HashRegistry,
    HashedRuntimeConfig, OutputBlock, OutputLeaf, PortablePath, StrictModel,
)
from honbu.flow import CommandSpec, ComposeSpec, ProjectSpec, StepSpec, TargetSpec, load_config, open_run, resolve

class Outputs(OutputBlock):
    dir_template: Annotated[str | None, DIRECTIVE] = "{experiment.name}/{runtime.run_id}"
    checkpoints: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir="checkpoints"))

class RootConfig(StrictModel):
    experiment: ExperimentConfig
    runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    epochs: int = 10

PROJECT = ProjectSpec(
    root_type=RootConfig,
    compose_spec=ComposeSpec(packaged="myproj.config_defaults"),
    hashes=HashRegistry([FieldHash("config_hash", include=("runtime", "epochs"), version="1")], root_type=RootConfig),
)
TRAIN = CommandSpec(
    name=("train",),
    target=TargetSpec("outputs.dir_template", "outputs.dir", "output_root"),
    hashes=("config_hash",),
    steps=(StepSpec("fit"),),
)

loaded = load_config("default", project=PROJECT, command=TRAIN)
res = resolve(loaded.composed, command=TRAIN, project=PROJECT)
with open_run(res, composed=loaded.composed, project=PROJECT) as run:
    if not run.skip("fit"):
        with run.step("fit"):
            run.record_best(epoch=run.config.epochs, metrics={"loss": 0.01})
```

See the complete runnable example in [`examples/linefit/project.py`](examples/linefit/project.py).

## Documentation

Browse the docs directly in [`docs/`](docs/index.md), or render the Material site locally with `pip install -e ".[docs]" && mkdocs serve`:

- **Start here:** [Overview](docs/index.md) · [Tutorial (empty dir to resumable run)](docs/tutorial.md) · [Adopt an existing project](docs/how-to/adopt.md)
- **Concepts:** [Why Honbu exists](docs/concepts/index.md) · [Configuration](docs/concepts/configuration.md) · [Identity](docs/concepts/identity.md) · [Runs](docs/concepts/runs.md) · [Refusals](docs/concepts/refusals.md)
- **How-to guides:** [Configure](docs/how-to/configure.md) · [Commands](docs/how-to/commands.md) · [Hashes](docs/how-to/hashes.md) · [Paths](docs/how-to/paths.md) · [Objectives](docs/how-to/objective.md) · [Materialize & drift](docs/how-to/materialize.md) · [Compare runs](docs/how-to/compare.md) · [Sweeps](docs/how-to/sweeps.md)
- **Reference:** [CLI](docs/reference/cli.md) · [Markers](docs/reference/markers.md) · [Hashes](docs/reference/hashes.md) · [Templates](docs/reference/templates.md) · [Manifest](docs/reference/manifest.md) · [API](docs/reference/api.md) · [Glossary](docs/reference/glossary.md)

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests examples
mkdocs build --strict
```

## License

MIT
