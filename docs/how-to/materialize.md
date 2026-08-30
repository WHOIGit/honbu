# Materialize a starter tree

`init` copies your packaged defaults into a working directory so a user can edit
them. Honbu plans that copy from the compositor's own recorded selections rather
than from a list you maintain.

## Declare a scope

```python
from honbu.materialize import (
    MaterializationRoot,
    MaterializationScope,
    apply_materialization,
    plan_config_materialization,
)

SCOPES = {
    "default": MaterializationScope(
        "default",
        config_roots=(
            MaterializationRoot("config"),
            MaterializationRoot("config", overrides=("sweep=example",)),
        ),
    )
}
```

```python
plan = plan_config_materialization(
    packaged_root,
    destination / "configs",
    compose_spec=CONFIG,
    scopes=SCOPES,
    defaults=("default",),
    overwrite=overwrite,
    cwd=destination,
)
apply_materialization(plan)
for item in plan.files:
    print(f"{item.action:9} {item.destination}")
```

## Why two roots

A root is composed, and the files Hydra *selected* become the closure to copy. A
group option nobody selected is not in that closure — so a project shipping a sweep
definition, an alternative dataset, or any other group alternative needs a second
root that selects it, or the starter tree silently documents the feature by omitting
it.

That is also why the plan is built by asking the compositor rather than by walking
the packaged directory: walking would copy files that are not reachable, and
re-implementing the defaults-list algorithm would give you a second, eventually
divergent, answer to which files matter.

## What it will not do

`apply_materialization` never deletes and never overwrites unless you pass
`overwrite=True`. Every planned file carries an action, so you can print the plan
before applying it — `create`, `skip`, `overwrite` — and a user can see what an
`init` into a non-empty directory is about to do.
