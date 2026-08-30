# Retype paths

Every filesystem path in your configuration should be `PortablePath`. This page is
about doing that to a schema that already exists.

## Why the type, not a convention

```python
class DataConfig(StrictModel):
    points: PortablePath          # not: Path, not: str
```

A marked path gets three behaviors that a plain `Path` does not:

1. **Anchored during resolution.** Relative values become absolute against the
   working directory, once, in a known phase — so nothing downstream has to guess
   what a relative path was relative to.
2. **Two views.** Absolute in `resolved.yaml`, relative in `composed.yaml`. The first
   is what ran here; the second is what a colleague can run there.
3. **Excluded from every digest.** `/home/ana/reef` and `/scratch/reef` are the same
   dataset. If the path were hashed, moving the data would change the identity of
   every experiment that used it, and two people running the same study would never
   agree.

The third is the one that silently matters. A `Path` field that should have been
`PortablePath` produces runs whose digests are machine-specific — everything works,
and nothing compares.

## The `./` rule

A leading `./` or `../` means **the working directory**. A bare relative path means
**the root the declaration names** — for a run's target, the `output_root` its
command declares.

```yaml
output_root: ./runs                          # relative to the shell
outputs:
  dir_template: "{experiment.name}/{runtime.run_id}"   # relative to output_root
```

This is why the distinction is read off the authored string and not off a `Path`:
`Path("./runs")` is `PosixPath("runs")` — pathlib discards the prefix, so a `Path`
cannot carry the answer. Use `honbu.is_cwd_relative` if you are anchoring a
configured path of your own.

## Converting an existing schema

1. **Find them.** Every field whose value is a location: datasets, checkpoints,
   caches, output roots, vocabularies, label maps.
2. **Retype.** `Path` and `str` both become `PortablePath`. Existing YAML needs no
   change.
3. **Check the digest.** Recompute a digest for a known configuration before and
   after. If it changed, a path was in your identity and now is not — which is the
   fix, but it means old digests do not compare to new ones. Bump the hash's
   `version` to say so out loud.
4. **Remove your own anchoring.** Any `path.resolve()` or `os.path.join(root, ...)`
   in your code is now doing a second time what resolution did once, and the two can
   disagree.

!!! warning "URIs are not paths"

    A field holding `s3://bucket/key` or `hf://dataset` is an identifier, not a
    location. Leave it a string so it stays in the digest — remote data has no
    machine-specific spelling to normalize away.

## What stays a plain string

- Model names and backend identifiers (`yolo11n.pt` as a *name*, not a file you own)
- URIs and registry references
- Anything whose spelling is part of the experiment

The test is not "does it look like a path" but "would the same experiment on another
machine spell this differently?" If yes, mark it. If no, leave it in the digest.
