# Tutorial: your first project

By the end of this page you will have a working project that composes
configuration, gives every run an identity, writes it somewhere safe, records what
produced it, and resumes where it died. It fits a line to points, so that nothing
on this page is about machine learning and everything is about Honbu.

The finished project is [`examples/linefit`](https://github.com/WHOIGit/honbu/blob/main/examples/linefit),
and every block below is quoted from it.

## 1. Install

```bash
pip install "honbu @ git+https://github.com/WHOIGit/honbu.git"
```

## 2. Lay out the project

```text
linefit/
├── project.py
├── config_defaults/
│   ├── __init__.py
│   └── config.yaml
└── data/points.csv
```

`config_defaults` is an importable package, not a loose directory. Committed
defaults ship *with the code* so a fresh checkout runs without a setup step, and a
`configs/` directory in the working directory shadows them group by group when
someone wants to change something.

## 3. Import what you need

```python
--8<-- "examples/linefit/project.py:imports"
```

## 4. Assemble the root

A root is an ordinary Pydantic model built from Honbu's blocks and your own. It is
strict — an unknown key is an error, not a shrug — and it stays **flat**, so
configuration groups and hash declarations address one namespace.

```python
--8<-- "examples/linefit/project.py:blocks"
```

Three things are doing real work here:

`PortablePath`
:   Marks a field as a filesystem path. Marked paths are absolute in `run.config`
    (the in-memory view), stay portable on disk, and are excluded from every digest —
    so moving the project to another machine does not change what a run *is*. See
    [Retype paths](how-to/paths.md).

`HashedRuntimeConfig`
:   An opt-in subclass of `RuntimeConfig` pre-wired with `config_hash` (`DERIVED`
    and `FromHash("config_hash")`) and `config_id = "{runtime.config_hash:coolname}"`.
    Authoring a value for a derived field is refused. See
    [Declare hashes](how-to/hashes.md).

`validate_root_contract`
:   Checks the requirable blocks at *import*, so a malformed root fails when you
    load the module rather than after twenty minutes of training.

## 5. Declare what a run is

A digest turns a configuration into a name. Declare what goes into it, once:

```python
--8<-- "examples/linefit/project.py:hashes"
```

`config_hash` covers what the configuration *says*. `dataset_hash` covers what the
inputs *are* — and because it reads inputs, it is **deferred**: it cannot be known
before the run opens, so the run completes it and the manifest records that it did.

## 6. Declare your commands and project

A `CommandSpec` declares its target, the steps it will take, its write scope, and
which configuration sources it accepts. `ProjectSpec` bundles the root type,
composition spec, and hash registry once so every operation shares them. Your CLI
(here Typer with `honbu.cli.presets`) stays yours.

```python
--8<-- "examples/linefit/project.py:commands"
```

## 7. Write the work

The command loads and resolves the configuration, then opens a live `Run`.
Everything inside the lifecycle — placing the directory, writing provenance,
completing deferred hashes, updating the manifest — happens through `open_run`:

```python
--8<-- "examples/linefit/project.py:handler"
```

```python
--8<-- "examples/linefit/project.py:main"
```

## 8. Run it

```bash
python -m linefit.project train
```

```text
runs/linefit/dynamic-macaw/
├── manifest.json
├── config/
│   ├── composed.yaml      # what you authored, before resolution
│   ├── resolved.yaml      # resolved portable view (relative outputs, assigned identities)
│   └── hashes/            # one artifact per digest
└── checkpoints/line.json
```

Open `manifest.json`. It records the state, an append-only timeline, every
invocation with its argv, the digests, the artifacts you registered, and the best
metrics you recorded. It is written by one writer — the run — so it cannot disagree
with itself.

Override anything from the command line:

```bash
python -m linefit.project train training.epochs=100 experiment.name=steeper
```

Run the same configuration twice and you get two runs, because the output template
names `{runtime.run_id}`, which is minted fresh per invocation. Point the template
at `{runtime.config_id}` instead and the same configuration would name one
directory, forever. That choice is yours and it is the subject of
[Identity](concepts/identity.md).

## 9. Kill it and bring it back

Interrupt the run mid-fit, then:

```bash
python -m linefit.project train --resolved runs/linefit/dynamic-macaw --resume
```

The completed steps are skipped, the interrupted one runs again, and the run
finishes. Nothing was written down by a driver to make that work: the run's own
manifest is the only record, which is why it is still true after a reboot.

## 10. Extend a run without disturbing it

```bash
python -m linefit.project report --resolved runs/linefit/dynamic-macaw
```

```python
--8<-- "examples/linefit/project.py:augmenting"
```

`report` is an **augmenting** command. It declares `scope=("outputs.reports",)`, so
it may replace the report and nothing else — `--clobber` on the report cannot reach
the checkpoints. The run gains a second invocation record rather than becoming a
new run.

## Where next

- [Configure a project](how-to/configure.md) — groups, precedence, local shadowing
- [Identity](concepts/identity.md) — what belongs in a digest, and why
- [Sweep configurations](how-to/sweeps.md) — turn one configuration into many runs
- [Adopt an existing project](how-to/adopt.md) — if you already have a trainer
