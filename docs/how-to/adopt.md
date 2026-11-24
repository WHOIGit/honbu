# Adopt an existing project

You already have a trainer that works. This is the order to do it in so that each
step stands alone and you can stop after any of them.

Do not start by rewriting your CLI. Start with identity, because that is the part
your project almost certainly gets subtly wrong and cannot tell.

## 1. Make the schema strict

Assemble a `StrictModel` root from Honbu's requirable blocks (`ExperimentConfig`,
`RuntimeConfig` or `HashedRuntimeConfig`, `OutputBlock`, `OutputLeaf`) and your
existing configuration blocks, keeping it flat. Add `validate_root_contract(RootConfig)`.

Expect this step to find things: keys nobody sets any more, two spellings of one
option, a default that never validated. Strictness turns a typo'd override from a
silent no-op into an error, which is most of the value on its own.

### Derived domain fields

Sometimes a domain block computes a derived convenience field from other authored
fields during validation — for instance, deriving an `inference_pipeline` transform
list from the authored training transforms in `model_validator(mode="after")`.

Mark that field with **`RESOLVED_ONLY` without `DERIVED`**:

```python
from typing import Annotated
from pydantic import model_validator
from honbu import RESOLVED_ONLY, StrictModel

class TransformConfig(StrictModel):
    train_pipeline: list[str]
    inference_pipeline: Annotated[list[str] | None, RESOLVED_ONLY] = None

    @model_validator(mode="after")
    def _derive_inference(self) -> "TransformConfig":
        if self.inference_pipeline is None:
            self.inference_pipeline = [t for t in self.train_pipeline if t != "random_flip"]
        return self
```

Why `RESOLVED_ONLY` *without* `DERIVED`?

- **`RESOLVED_ONLY`** refuses authored values on fresh runs (`replay=False`) and
  strips the field from `composed.yaml`, while preserving it in `resolved.yaml` and
  accepting it on `--resolved` replays.
- Omitting **`DERIVED`** keeps the field *inside* behavioral digests if its parent
  block is hashed (or pair with `DERIVED` if it is purely redundant accounting).

**Stop here and you already have:** a configuration that is one declared thing rather
than a dictionary of conventions.

## 2. Retype the paths

Every filesystem path becomes `PortablePath`. See [Retype paths](paths.md), including
what should *not* be converted.

Then delete your own path anchoring — the `resolve()` calls and root joins scattered
through the code — because resolution now does it once, in a known phase.

## 3. Declare the digests

Declare a `FieldHash` over the subtrees that decide behavior, and a `SourceHash` for
your dataset if its contents are part of what a run is. If your runtime block uses
`HashedRuntimeConfig`, `config_hash` and `config_id` are already wired to
`FromHash("config_hash")`.

Now compare: does your existing run-naming scheme agree with the digest about which
runs are "the same"? For most projects the answer is no, and the discrepancy is the
thing worth knowing. Common findings: the output directory is in the identity, the
dataset is not, or the identity is a timestamp and no two runs have ever been
recognized as equivalent.

**Stop here and you already have:** comparable runs, and a written answer to what
your experiment is.

## 4. Declare commands and adopt the run

Declare `CommandSpec`s for what your CLI already does and bundle your schema,
composition spec, and hashes into a `ProjectSpec`. Inside your existing CLI callbacks,
call `load_config` → `resolve` → `open_run` and move the work inside `run.step()`,
recording artifacts and best metrics through the run.

`honbu.flow` has no CLI framework dependency, so a project with a Typer, Click, or
`argparse` surface it likes keeps it. If you use Typer, `honbu.cli.presets` gives you
ready-made option aliases (`ConfigOpt`, `ResolvedOpt`, `ForkFromOpt`, `ClobberOpt`,
`ResumeOpt`, etc.) and `HYDRA_CONTEXT_SETTINGS`; for any other parser, `honbu.cli`
provides `split_dash_rule`, `validate_override_tokens`, `select_config_source`, and
`exit_on_refusal`. See [Register commands](commands.md).

What you delete here is usually substantial — run-directory naming, collision
handling, the resume flag, the manifest, the "did I already do this step" logic, and
the code that copied the config into the output directory.

**Stop here and you already have:** the whole run lifecycle, including resume and
provenance.

## 5. Take the operations you want

Sweeps, comparison, compatibility gating, and materialization are separate imports.
Add [sweeps](sweeps.md) when you want one configuration to become many runs; add
[comparison](compare.md) when you need to know why two runs differ or whether two
runs are compatible for merging; add [materialization](materialize.md) when users
should be able to scaffold and diff local config trees. Neither is required, and
nothing below them depends on them.

## What to expect

**Your run directories will move.** Digest-based naming is not your old naming.
Decide once whether the template names `{runtime.run_id}` (repeats accumulate) or
`{runtime.config_id}` (one configuration, one directory) — see
[Identity](../concepts/identity.md) — and accept that old runs live under the old
scheme.

**Some of your configuration will turn out not to be configuration.** Log levels,
worker counts and device selection are invocation details; putting them in the digest
makes every run unique. Either keep them out of the hashed subtrees or move them to
flags.

**A refusal early is the point.** The first week of adoption surfaces refusals for
things that "worked" before — an authored value in a derived field, a sweep handed to
a single command, two runs claiming one directory. Each one is a wrong answer you were
previously getting silently. See [Refusals](../concepts/refusals.md).

## A checklist

- [ ] Root is a `StrictModel`, flat, with the requirable blocks (`output_root` is `PortablePath`)
- [ ] `validate_root_contract` runs at import (or via `ProjectSpec`)
- [ ] Every path is `PortablePath`; no manual anchoring remains
- [ ] A `FieldHash` covers what decides behavior, and nothing that does not
- [ ] Input-reading digests declare `reads_inputs=True` and are completed in the run
- [ ] Identity fields are `DERIVED` and bound with `FromHash` (or use `HashedRuntimeConfig`)
- [ ] Each command declares its target, steps, scope, and configuration sources
- [ ] Expensive work is inside `run.step()`
- [ ] `record_best` is called, and a test asserts it
- [ ] `ProjectSpec` and `CommandRegistry` construct cleanly in a test
- [ ] The packaged defaults compose and validate in a test
