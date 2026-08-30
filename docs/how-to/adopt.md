# Adopt an existing project

You already have a trainer that works. This is the order to do it in so that each
step stands alone and you can stop after any of them.

Do not start by rewriting your CLI. Start with identity, because that is the part
your project almost certainly gets subtly wrong and cannot tell.

## 1. Make the schema strict

Assemble a `StrictModel` root from Honbu's requirable blocks and your existing
configuration blocks, keeping it flat. Add `validate_root_contract(RootConfig)`.

Expect this step to find things: keys nobody sets any more, two spellings of one
option, a default that never validated. Strictness turns a typo'd override from a
silent no-op into an error, which is most of the value on its own.

**Stop here and you already have:** a configuration that is one declared thing rather
than a dictionary of conventions.

## 2. Retype the paths

Every filesystem path becomes `PortablePath`. See [Retype paths](paths.md), including
what should *not* be converted.

Then delete your own path anchoring — the `resolve()` calls and root joins scattered
through the code — because resolution now does it once, in a known phase.

## 3. Declare the digests

Declare a `FieldHash` over the subtrees that decide behavior, and a `SourceHash` for
your dataset if its contents are part of what a run is.

Now compare: does your existing run-naming scheme agree with the digest about which
runs are "the same"? For most projects the answer is no, and the discrepancy is the
thing worth knowing. Common findings: the output directory is in the identity, the
dataset is not, or the identity is a timestamp and no two runs have ever been
recognized as equivalent.

**Stop here and you already have:** comparable runs, and a written answer to what
your experiment is.

## 4. Declare commands and adopt the run

Declare `CommandSpec`s for what your CLI already does, then move each entry point's
body into a handler that receives a `Run`. Wrap expensive phases in `run.step()` and
record artifacts and best metrics through the run.

You do not have to adopt Honbu's dispatcher to do this: `dispatch_command` works from
your own parser, so a project with a Typer surface it likes keeps it. See
[Register commands](commands.md).

What you delete here is usually substantial — run-directory naming, collision
handling, the resume flag, the manifest, the "did I already do this step" logic, and
the code that copied the config into the output directory.

**Stop here and you already have:** the whole run lifecycle, including resume and
provenance.

## 5. Take the operations you want

Sweeps, comparison and materialization are separate imports. Add
[sweeps](sweeps.md) when you want one configuration to become many runs; add
[comparison](compare.md) when you need to know why two runs differ. Neither is
required, and nothing below them depends on them.

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

- [ ] Root is a `StrictModel`, flat, with the requirable blocks
- [ ] `validate_root_contract` runs at import
- [ ] Every path is `PortablePath`; no manual anchoring remains
- [ ] A `FieldHash` covers what decides behavior, and nothing that does not
- [ ] Input-reading digests declare `reads_inputs=True` and are completed in the run
- [ ] Identity fields are `DERIVED` and bound with `FromHash`
- [ ] Each command declares its target, steps, scope and configuration sources
- [ ] Expensive work is inside `run.step()`
- [ ] `record_best` is called, and a test asserts it
- [ ] `APP.validate()` runs in a test
- [ ] The packaged defaults compose and validate in a test
