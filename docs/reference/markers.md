# Markers

A marker is metadata on a field's type. It is read by the digest walkers, the
portable/absolute projection, and comparison — so a marker is behavior, not a note to
the reader.

## The aliases you will use

| Alias | Expands to | Meaning |
| --- | --- | --- |
| `PortablePath` | `Annotated[Path, PORTABLE_PATH]` | a filesystem path |
| `ResolvedString` | a string only resolution may fill | authoring one is refused |
| `ResolvedPath` | a path only resolution may fill | authoring one is refused |
| `TemplatedIdentity` | a template-valued name | rendered during resolution |

## The markers themselves

| Marker | Excluded from digests | Purpose |
| --- | --- | --- |
| `PORTABLE_PATH` | yes | machine-specific location; stored absolute, authored relative |
| `DERIVED` | yes | Honbu writes this field |
| `TEMPLATED` | yes | a rendered label for the configuration, not an input |
| `DIRECTIVE` | yes | an instruction to the framework, authored by hand |
| `RESOLVED_ONLY` | no | may not be authored, but is part of the configuration |
| `IDENTITY` | no | records this field's value in the run manifest's identities (orthogonal to origin — e.g. `run_id`, `config_id`, `sweep_id`) |
| `FromHash(name)` | — | binds the field to a declared hash |

The four exclusions differ in *why*. Three are about provenance — Honbu generated it,
it is a location, it is a template. `DIRECTIVE` is about audience: the field is
authored by hand and is useful provenance to keep, it is simply not something the run
reads. A sweep block is the case in point.

## Checking your own

```python
from honbu.core.markers import field_marker, check_field_path

field_marker(RootConfig.model_fields["data"], PathMarker)   # the marker, or None
check_field_path(RootConfig, "data.points")                 # a PathProblem, or None
```

`check_field_path` does the schema check and the composed-data check in one traversal,
which is what lets a refusal say whether a path is wrong in the schema or merely absent
from this configuration.
