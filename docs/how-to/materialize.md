# Materialize a starter tree

`init` copies your packaged config defaults into a working directory so a user can
edit them locally. Honbu plans that copy from the compositor's own recorded
selections rather than from a list you maintain by hand.

## Declare a scope

```python
from honbu.materialize import (
    MaterializationRoot,
    MaterializationScope,
    apply_materialization,
    plan_materialization,
    render_plan,
)

SCOPES = {
    "default": MaterializationScope(
        "default",
        roots=(
            MaterializationRoot("config"),
            MaterializationRoot("config", overrides=("sweep=example",)),
        ),
    )
}
```

```python
plan = plan_materialization(
    destination / "configs",
    project=PROJECT,          # or compose_spec=CONFIG
    scopes=SCOPES,
    defaults=("default",),
    overwrite=overwrite,
    cwd=destination,
)
print(render_plan(plan))
apply_materialization(plan)
```

`plan_materialization` derives the packaged root directly from `compose_spec` (or
`project.compose_spec`), resolves each root's Hydra dependency closure (including
`@package`-qualified groups), and excludes Python package plumbing (`__init__.py`,
`__pycache__/`, `*.pyc`, `*.pyo`) automatically when copying whole directories via
`files=(...)`.

To copy only the packaged dependencies referenced by an external authored YAML file
while leaving that file where it is, pass `MaterializationRoot.from_file(path)`:

```python
MaterializationScope(
    "external",
    roots=(MaterializationRoot.from_file(Path("my_experiment.yaml")),),
)
```

## Why two roots

A root is composed, and the files Hydra *selected* become the closure to copy. A
group option nobody selected is not in that closure — so a project shipping a sweep
definition, an alternative dataset, or any other group alternative needs a second
root that selects it, or the starter tree silently documents the feature by omitting
it.

That is also why the plan is built by asking the compositor rather than by walking
the packaged directory: walking would copy unreachable files, and re-implementing
the defaults-list algorithm would give you a second, eventually divergent, answer to
which files matter.

## Check local drift

Compare a local `configs/` directory against the packaged defaults at either the
composed-configuration level or the file level:

```python
from honbu.materialize import (
    compare_materialized_config,
    materialized_file_drift,
    render_file_drift,
)
from honbu.compare import render_comparison

# Composed-config drift (works even for partial local trees that omit config.yaml)
config_drift = compare_materialized_config(
    destination / "configs",
    project=PROJECT,
    level="behavioral",
    overrides=("training.epochs=50",),
)
print(render_comparison(config_drift))

# File-by-file drift: modified, unchanged, local_only, packaged_only
file_drift = materialized_file_drift(destination / "configs", project=PROJECT)
print(render_file_drift(file_drift))
```

## Mount as a Typer command

If your CLI uses Typer, `honbu.cli.presets.build_materialize_command` returns a
ready-made command function supporting `--scope`, `--dest`, `--overwrite`, and
`--dry-run` that you can register under any name you choose (`init`, `config-init`,
etc.):

```python
from honbu.cli.presets import build_materialize_command

APP.command("init")(
    build_materialize_command(
        project=PROJECT,
        scopes=SCOPES,
        defaults=("default",),
    )
)
```

## What it will not do

`apply_materialization` never deletes and never overwrites unless you pass
`overwrite=True`. Every planned file carries an action (`create`, `skip`,
`overwrite`), and `plan.created`, `plan.skipped`, and `plan.overwritten` let you
inspect or display the plan before applying it.
