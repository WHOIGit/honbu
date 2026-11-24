# The manifest

`manifest.json` is the live record of a run, written by the run and by nothing else.

| Key | Holds |
| --- | --- |
| `state` | derived from the timeline; never assigned |
| `timeline` | append-only events, each with a UTC timestamp |
| `invocations` | one entry per command that touched this run: argv, command, configuration index |
| `hashes` | each declared digest: value, recipe version, and for a deferred one, the inputs reported |
| `artifacts` | name → path, relative when inside the run |
| `progress` | incrementally merged via `record_progress` (or replaced wholesale via `set_progress`) |
| `best` | the last `record_best` |
| `parent` | lineage (`run_dir`, `config_hash`, `artifact_key`, `artifact`), when forked via `--fork-from` |

## States

| State | Reached by |
| --- | --- |
| `initialized` | the directory and provenance exist; no work has run |
| `running` | a step is in progress |
| `partial` | work stopped cleanly with steps outstanding |
| `completed` | every declared step finished |
| `interrupted` | a signal arrived |
| `failed` | an exception escaped |

Everything but `completed` is resumable. `Manifest.load(run_dir)` derives `state`
from the last reserved event on the timeline by default (`derive_state=True`), so it
cannot disagree with the timeline.

## Reading one

```python
from honbu.flow import Manifest

manifest = Manifest.load(run_dir)
manifest.state                      # 'completed'
manifest.data["best"]["metrics"]    # what the run achieved
manifest.data["invocations"][0]["argv"]
```

Lifecycle events are reserved: `record_event` refuses to write one, so a project
cannot forge a state it did not reach.

## What is beside it

```text
config/composed.yaml         what was authored, portable
config/resolved.yaml         what ran, absolute
config/hashes/<name>.json    one artifact per digest
```

The bundle is written before your handler is called, so a run that dies immediately
still says what it was going to do.
