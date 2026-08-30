# Register commands

A command is a declaration. From it Honbu derives the argv split, the help text, the
resolution order, the collision check, and the blast radius of `--clobber` — so
there is exactly one place to change when the command changes.

## Declare and register

```python
--8<-- "examples/linefit/project.py:commands"
```

Registration is a decorator. The handler receives a live `Run`:

```python
--8<-- "examples/linefit/project.py:handler"
```

```python
--8<-- "examples/linefit/project.py:main"
```

## What a `CommandSpec` says

| Field | Meaning |
| --- | --- |
| `name` | the command path — `("eval", "test")` registers `eval test` |
| `mode` | `"create"` (default) or `"augment"` |
| `target` | which fields hold the output template, the resolved directory, and the root |
| `hashes` | which declared digests this command computes |
| `steps` | the units of "do not do this again" |
| `scope` | for an augmenting command, the output leaves it may replace |
| `config_sources` | which of `default`, `file`, `resolved`, `continue` it accepts |
| `positionals` | required and optional positional arguments |
| `advances_run` | whether finishing moves the run's state |
| `continue_artifact` | which artifact `--continue-from` reads |

Nested names work as you would expect: register `eval validation` and `eval test`
and each gets its own scope, so `--clobber` on the test report cannot erase the
validation results. Honbu matches the longest registered path first.

## Producing versus augmenting

A **producing** command creates a run: it resolves a fresh target, checks it is
safe, writes the provenance bundle, and advances the state.

An **augmenting** command names a run that exists, via `--resolved`, and adds to it.
Declare `mode="augment"`, point `target` at the same target spec, and give it the
narrow `scope` it is allowed to replace:

```python
--8<-- "examples/linefit/project.py:augmenting"
```

The reference may be a run directory or its `config/resolved.yaml`, relative or
absolute.

## Configuration sources

Exactly one source is in play per invocation, declared per command:

| Source | Flag | Gives you |
| --- | --- | --- |
| `default` | *(none)* | composed from the search path |
| `file` | `--config x.yaml` | that file, composed with its own directory as the search path |
| `resolved` | `--resolved <run>` | a prior run's resolved configuration — a replay, landing in that same run |
| `continue` | `--continue-from <run>` | a prior run's *authored* configuration, as a new run with recorded lineage |

`--resolved` and `--continue-from` differ in which view they read, and the
difference matters: a replay keeps the absolute paths and the literal output
directory, so it re-enters the same run; a continuation builds on the portable form,
so it survives being moved to another machine.

## Steps and resume

```python
if not run.skip("fit"):
    with run.step("fit"):
        ...
```

Declare a step where repeating the work would be expensive or destructive. A step
that completed is skipped on `--resume`; the one that was interrupted runs again.
Declaring a step per epoch is usually wrong — the boundary is a promise the work
either happened or did not, and fine-grained promises are harder to keep than the
repetition costs.

`StepSpec` takes `on_resume="restart"` for a step that must always re-run (a cheap
setup step, say) and a `scope` for one that owns particular output leaves.

## Keeping your own parser

Honbu's dispatcher is optional. `dispatch_command` threads one command from its
remaining argv tokens to a finished run and knows about no CLI library:

```python
from honbu.flow import dispatch_command

dispatch_command(
    handler, tokens,
    root_type=RootConfig, hashes=HASHES, compose_spec=CONFIG,
)
```

A project with a Typer or Click surface it likes calls this from its own callback
and keeps everything else. What you lose is generated help and shell completion;
what you keep is the whole resolution and run lifecycle.

If you route commands yourself *above* Honbu — deciding between two commands by
reading the configuration, for instance — you take on three things the dispatcher
was doing: refusals must print as messages and exit 1 rather than raising a
traceback, `KeyboardInterrupt` should exit 130, and `--help` must still reach the
generated help.

## Validate the declarations

```python
APP.validate()
```

Runs every declaration check at once — target paths that exist in the schema, hash
names that are declared, deferred values absent from target templates, `--continue-from`
paired with an artifact to continue from. Call it in a test, and a malformed command
fails at import rather than at `rmtree`.
