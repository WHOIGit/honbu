# Register commands

A `CommandSpec` declares what a command does to a run: its mode, target, hashes,
steps, accepted configuration sources, and write scope. From that declaration Honbu
drives configuration loading, resolution order, collision checks, and the blast
radius of `--clobber` — without owning your CLI parser.

## Declare commands and project

```python
--8<-- "examples/linefit/project.py:commands"
```

Your CLI callback loads the configuration, resolves it, and opens a live `Run`:

```python
--8<-- "examples/linefit/project.py:handler"
```

```python
--8<-- "examples/linefit/project.py:main"
```

## What a `CommandSpec` says

| Field | Meaning |
| --- | --- |
| `name` | the command path — `("eval", "test")` names `eval test` |
| `mode` | `"produce"` (default) or `"augment"` |
| `target` | which fields hold the output template, the resolved directory, and the root |
| `hashes` | which declared digests this command computes |
| `steps` | the units of "do not do this again" |
| `completion` | which steps satisfy completion (defaults to all `steps`) |
| `scope` | for an augmenting command, the output leaves it may replace |
| `config_sources` | which of `default`, `file`, `resolved`, `fork` it accepts |
| `advances_run` | whether finishing moves the run's state |
| `fork_artifact` | optional default artifact key recorded when forking (`--fork-from`) |

Give nested subcommands distinct scopes — for example `eval validation` and
`eval test` — so `--clobber` on the test report cannot erase validation results.

## Producing versus augmenting

A **producing** command (`mode="produce"`) creates a run: it resolves a fresh target,
checks it is safe, writes the provenance bundle, and advances the run state.

An **augmenting** command (`mode="augment"`) names a run that already exists, via
`--resolved`, and adds to it. Point `target` at the same `TargetSpec` and declare the
narrow `scope` it is allowed to replace:

```python
--8<-- "examples/linefit/project.py:augmenting"
```

The reference may be a run directory or its `config/resolved.yaml`, relative or
absolute.

## Configuration sources

Exactly one source is in play per invocation, declared per command:

| Source | Conventional flag | Gives you |
| --- | --- | --- |
| `default` | *(none)* | composed from the search path |
| `file` | `--config x.yaml` | that file, searching its own directory, then `--config-dir`, `./configs`, and packaged defaults |
| `resolved` | `--resolved <run>` | a prior run's resolved configuration — a replay, landing in that same run |
| `fork` | `--fork-from <run>` | a prior run's *authored* configuration, as a new run with recorded lineage |

`--resolved` and `--fork-from` differ in which view they read, and the difference
matters: a replay keeps absolute paths and the literal output directory, so it
re-enters the same run; a fork builds on the portable `config/composed.yaml`, so it
survives being moved to another machine and records `parent` lineage (including the
optional `artifact_key` selected via `--fork-artifact` or `CommandSpec.fork_artifact`).

## Steps and resume

```python
if not run.skip("fit"):
    with run.step("fit"):
        ...
```

Declare a step where repeating the work would be expensive or destructive. A step
that completed (or was marked `not_applicable`) is skipped on `--resume`; the one
that was interrupted runs again. Declaring a step per epoch is usually wrong — the
boundary is a promise the work either happened or did not, and fine-grained promises
are harder to keep than the repetition costs.

`StepSpec` takes `on_resume="restart"` for a step whose partial output from an
interrupted or failed run is spoiled work and should be cleared before retrying
(completed steps are still skipped via `run.skip()`), vs. the default `"continue"`
for steps whose prior output (such as checkpoints) is resume state. Pass `scope` to
name the output leaves a restarting step owns.

On `--resume`, Honbu preserves the recorded `run_id` when not explicitly overridden
and refuses behavioral `config_hash` drift by default (`allow_config_drift=False`),
directing you to `--fork-from` or `--clobber`.

## Keeping your own CLI parser

`honbu.flow` is completely CLI-free — `load_config`, `resolve`, `parent_record`, and
`open_run` take plain Python arguments and know about no CLI library.

- Using **Typer**? Import `HYDRA_CONTEXT_SETTINGS`, ready-made option aliases
  (`ConfigOpt`, `ResolvedOpt`, `ForkFromOpt`, `ForkArtifactOpt`, `ConfigDirsOpt`,
  `ClobberOpt`, `ResumeOpt`), or customizable option factories from
  `honbu.cli.presets`.
- Using **Click, `argparse`, or your own parser**? Import the library-agnostic
  helpers from `honbu.cli`: `split_dash_rule`, `validate_override_tokens`,
  `select_config_source`, `exit_on_refusal`, and `emit_warnings`.

Wrap your command entrypoint in `with exit_on_refusal(prog="yourproject"):` so
refusals print cleanly without tracebacks (exit 1), `KeyboardInterrupt` exits 130,
and unexpected defects preserve their full traceback.

## Validate declarations early

Constructing `ProjectSpec` checks `validate_root_contract` and
`validate_identity_fields` immediately, and constructing a `CommandRegistry` checks
every command declaration at once — target paths in the schema, declared hash names,
and scope validity:

```python
from honbu.flow import CommandRegistry

COMMANDS = CommandRegistry([TRAIN, REPORT], root_type=RootConfig, hashes=HASHES)
```

Keep that construction at import time (or assert it in a test) so a malformed command
fails immediately rather than at `rmtree`.
