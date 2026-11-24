# Sweep configurations

A sweep is not a hidden multirun mode. It is a set of ordinary runs, prepared before
any of them starts, each one indistinguishable from the same configuration invoked by
hand.

That property is the design: a sweep child's digest equals the digest of that
configuration run alone, so a child can be compared against a hand-run baseline
without the comparison knowing it came from a sweep.

## Declare the block

```python
from honbu.sweeps import SweepBlock, SweepDeclaration

class RootConfig(StrictModel):
    ...
    sweep: SweepDeclaration = Field(default_factory=SweepBlock)
```

`SweepDeclaration` carries the `DIRECTIVE` marker, which keeps the whole block out of
every digest. The marker is not optional and Honbu verifies it rather than trusting
it: forgetting it fails silently — every run succeeds, and only the identities are
wrong.

A project may **extend** the block with fields of its own (or include a
`SweepOutputBlock` so Honbu resolves the sweep directory itself), and Honbu reads it
through the type you declared:

```python
from honbu.sweeps import SweepBlock, SweepOutputBlock

class ProjectSweep(SweepBlock):
    name: str = "default"
    outputs: SweepOutputBlock = Field(default_factory=SweepOutputBlock)

class RootConfig(StrictModel):
    sweep: Annotated[ProjectSweep, DIRECTIVE] = Field(default_factory=ProjectSweep)
```

## Declare the axes

```yaml
sweep:
  gate: runtime.config_id
  axes:
    training.epochs: [10, 20]
  covariant_axes:
    resolution:
      - {training.image_size: 320, training.batch: 16}
      - {training.image_size: 640, training.batch: 8}
```

Every axis contributes exactly **one dimension**, whatever its kind, so that is
2 × 2 = 4 children.

`axes`
:   A configuration path to the values it takes (at least one value; empty lists are
    refused). A path naming a block-typed field with a list of names is a *group*
    axis and selects whole files during composition.

`covariant_axes`
:   A label to options (at least two non-empty mappings with identical key sets) that
    each assign several paths at once. Two parameters that must move together
    describe a diagonal, and the diagonal of a grid is a different object than the
    grid — as separate axes, the example above would be four combinations, two
    unasked-for and one that runs out of memory.

Axes can also be declared entirely on the command line, with no addition prefix:

```bash
yourproject train sweep.axes.training.epochs=[10,20]
```

## Choose the gate

The **gate** is the configuration path whose value distinguishes children, and it must
appear in the child output template. Honbu checks three things before writing
anything:

- that the gate (and the resolved child target directory) is **stable** — it resolves
  the same under two different invocations differing in clock and entropy, so a value
  minted per invocation is refused;
- that it **distinguishes** every pair of children, naming the colliding children and
  the axes whose values differ between them when it does not;
- that the resolved child directories are themselves distinct, since a lossy template
  function can collapse two sound gate values into one directory.

`runtime.config_id` is the usual answer: a name for the configuration digest, so it
differs exactly when the children differ.

**The ordinary output template will not do.** A template naming `{runtime.run_id}` is
fresh per invocation, so preparation would put children somewhere new every time and
the sweep could be neither resumed nor grown.

## Prepare, then execute

Pass `project=PROJECT` (or `compose_spec=CONFIG` plus `root_type` and `hashes`) so
group axes compose automatically with your non-sweep overrides and `extra_dirs`
forwarded, and either pass an explicit `sweep_dir` or let `sweep_target` resolve a
`SweepOutputBlock`:

```python
from honbu.flow import TargetSpec
from honbu.sweeps import prepare_sweep, execute_sweep, child_argv

preparation = prepare_sweep(
    composed,
    sweep_target=TargetSpec(
        template="sweep.outputs.dir_template",
        resolved="sweep.outputs.dir",
        root="output_root",
    ),
    # or explicit: sweep_dir=sweep_dir,
    project=PROJECT,
    command=TRAIN,
    overrides=overrides,
)

result = execute_sweep(
    preparation.sweep_dir,
    lambda child: run_train_cli(child_argv(child)),
    on_error="continue",   # default "stop"; always propagates KeyboardInterrupt
)
```

Preparation expands the matrix, resolves every child, skips combinations rejected by
cross-field `@model_validator` rules (recording them in `preparation.skipped` and
`config/index.json`, while still refusing field-level typos and zero-valid sweeps),
checks the gate across all valid children, and only then writes each child's run
directory in the `initialized` state.

Execution then runs every outstanding child (or a validated subset via `only=`, which
accepts child indices or gate values). `child_argv(child)` builds the uniform
invocation for every child — `["--resolved", "<child>", "--resume"]` by default, with
customizable `resolved_flag` and `resume_flag` if your CLI spells them differently.

## Resume, and grow

The driver holds no state. Each child's own manifest says whether it completed, so
re-invoking skips what is done and resumes what was interrupted — after a crash, a
reboot, or a deliberate "stop, I need the GPU".

A sweep is identified by its **directory**, never by its definition. Add a value to an
axis and prepare again: only the child you gained is written, everything existing
stays where it is, and the sweep keeps its identity. Remove a value and nothing is
deleted — the child is retained, marked outside the current definition (`retired` /
`retired_missing` in `SweepStatus`), so results that exist stay reachable.

## Aggregating results

Use `honbu.compare.compare_sweep` to gate child compatibility and rank children over
their recorded `best.metrics`, or read each child's manifest directly if your project
computes a custom multi-epoch summary. See [Compare configurations and runs](compare.md)
and [Record an objective](objective.md).
