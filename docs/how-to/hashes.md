# Declare hashes and identities

## The shape

```python
--8<-- "examples/linefit/project.py:hashes"
```

A `HashRegistry` is built once, at import, with the root type. Declared paths are
validated then and there, so a hash naming a field you renamed fails when the module
loads rather than after the run directory is made.

## Choosing a form

`FieldHash(name, include=(...))`
:   Digest over named subtrees of the configuration. This is the usual one: it is
    what "the same configuration" means. Excluded markers are removed before
    hashing, so it covers behavior rather than spelling.

`SourceHash(name, build=...)`
:   Digest over a mapping you build. Use it when the thing you are identifying is
    not a subtree — a dataset's contents, a preprocessing recipe, an external
    version. Pass `reads_inputs=True` when building it requires reading files.

`ByteHash(name, ...)`
:   Digest over bytes you supply. Use it for a file whose contents are the identity —
    a vocabulary, a label map, a serialized index.

`FannedHash(name, ...)`
:   One entry per key of a mapping: a digest per split, per fold, per language. Each
    lands in its own artifact named after the key, the key is recorded inside, and
    colliding filenames are refused.

Give every declaration a `version`. Bumping it is how you say "the recipe changed,
old digests are not comparable to new ones" — which is a different statement from
"the configuration changed", and needs to be sayable on its own.

## Bind an identity to a digest

```python
config_hash: Annotated[ResolvedString, DERIVED, FromHash("config_hash")] = None
config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"
```

`FromHash` is checked against the registry at import, and refused on a field that is
not resolved-only — because a field someone can author is a field the framework
cannot honestly claim to have derived.

`config_id` is then a name for that digest: short, filesystem-safe, sayable out
loud. Use the digest for comparison and the identity for directories.

## Deferred digests

A hash that reads inputs cannot be computed during resolution. Declare it, and Honbu
records it as deferred; complete it where the inputs are first read:

```python
run.complete_hash("dataset_hash", inputs={"points": len(points)})
```

Deferral is declared rather than discovered, so the deferred set is the same on every
machine, and a target template naming a deferred value is refused at declaration
time — the directory has to exist before the work that computes the digest can run.

## What each hash costs you

A digest is a promise that two runs sharing it are the same experiment. Every field
you include is a field that, when changed, makes results incomparable — correctly if
it changes behavior, gratuitously if it does not. So:

- **Include** what changes the computation: hyperparameters, dataset identity, model
  definition, preprocessing.
- **Exclude** what changes the accounting: output locations, run names, log levels,
  worker counts, device selection.

The markers already exclude paths, derived values, templates and directives. What is
left is a judgement, and it is worth making deliberately once rather than discovering
later that every run in a project has a unique digest and none of them compare.
