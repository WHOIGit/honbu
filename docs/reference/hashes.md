# Hashes

## Declaration forms

| Form | Constructor | Covers |
| --- | --- | --- |
| Field | `FieldHash(name, include=(...), version=...)` | named subtrees of the configuration |
| Source | `SourceHash(name, build=..., reads_inputs=False)` | a mapping you build |
| Byte | `ByteHash(name, ...)` | bytes you supply |
| Fanned | `FannedHash(name, ...)` | one entry per key of a mapping |

## The registry

```python
HASHES = HashRegistry([...], root_type=RootConfig)
```

Declared paths are validated at construction, against the root type. `deferred_names`
gives the static set of digests that cannot be computed before a run opens.

```python
result = HASHES.compute({"config": model})
result.entries["config_hash"].value      # 'sha256:1f3a...'
```

## What is excluded

Before a `FieldHash` is computed, the included subtrees are projected: every field
carrying `PORTABLE_PATH`, `DERIVED`, `TEMPLATED` or `DIRECTIVE` is removed. What
remains is the behavioral configuration. See [Markers](markers.md).

## Artifacts

Each computed digest is written under the run's `hashes/` directory as its own
artifact, recording the value, the recipe version, and — for a deferred hash — the
inputs reported when it was completed. A fanned hash writes one file per key, with the
key sanitized for the filesystem and recorded inside; colliding filenames are refused
rather than silently merged.

Verification is tri-state: a digest can match, mismatch, or be unavailable, and those
are three different answers.

## Versions

Every declaration takes a `version`. Bump it when the *recipe* changes — when you
start including a field, change how a source mapping is built, or fix a bug in what
was covered. It says "old digests are not comparable to new ones", which is a
different statement from "the configuration changed" and needs to be sayable on its
own.

A framework release never bumps your recipe versions. That is guarded by a test in
Honbu itself.
