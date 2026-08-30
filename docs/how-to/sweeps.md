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

A project may **extend** the block with fields of its own, and Honbu reads it through
the type you declared:

```python
class ProjectSweep(SweepBlock):
    name: str = "default"
    dir: str = "./sweeps/{experiment.name}/{sweep.name}"

class RootConfig(StrictModel):
    sweep: Annotated[ProjectSweep, DIRECTIVE] = Field(default_factory=ProjectSweep)
```

Honbu still reads only the gate and the two axis containers; your fields are yours to
interpret.

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
:   A configuration path to the values it takes. A path naming a block-typed field
    with a list of names is a *group* axis and selects whole files during composition.

`covariant_axes`
:   A label to options that each assign several paths at once. Two parameters that
    must move together describe a diagonal, and the diagonal of a grid is a different
    object than the grid — as separate axes, the example above would be four
    combinations, two unasked-for and one that runs out of memory.

Axes can also be declared entirely on the command line, with no addition prefix:

```bash
yourproject train sweep.axes.training.epochs=[10,20]
```

## Choose the gate

The **gate** is the configuration path whose value distinguishes children, and it must
appear in the child output template. Honbu checks three things before writing
anything:

- that the gate is **stable** — it resolves the same under two different invocations,
  so a value minted per invocation is refused;
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

```python
from honbu.sweeps import prepare_sweep, execute_sweep, child_argv

preparation = prepare_sweep(
    composed,
    sweep_dir=sweep_dir,
    root_type=RootConfig,
    command=TRAIN,
    hashes=HASHES,
    context=context,
)

execute_sweep(sweep_dir, lambda child: APP.dispatch(["train", *child_argv(child)]))
```

Preparation expands the matrix, resolves every child, checks the gate across all of
them, and only then writes each child's run directory in the `initialized` state. The
ordering is the point: a collision found after eight children exist is eight
directories to reason about.

Execution then runs every outstanding child. `child_argv` gives the same invocation
for every child whatever state it is in — `--resolved <child> --resume` — which is why
a driver can be a `for` loop and a scheduler array over the child directories needs no
glue.

## Resume, and grow

The driver holds no state. Each child's own manifest says whether it completed, so
re-invoking skips what is done and resumes what was interrupted — after a crash, a
reboot, or a deliberate "stop, I need the GPU".

A sweep is identified by its **directory**, never by its definition. Add a value to an
axis and prepare again: only the child you gained is written, everything existing
stays where it is, and the sweep keeps its identity. Remove a value and nothing is
deleted — the child is retained, marked outside the current definition, so results
that exist stay reachable.

## Aggregating results

Honbu does not aggregate for you, because what "the result" is belongs to the project.
Read each child's recorded `best` from its manifest, or read your backend's own
per-epoch record if you want the best epoch rather than the last. See
[Record an objective](objective.md) for why that seam is where it is.

The [object-detection example](../examples/object-detection.md) does exactly this: a
ranked table and a comparison figure written beside the sweep.
