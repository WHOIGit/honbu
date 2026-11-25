# Templates

Honbu renders `{...}` tokens in output templates and identity fields. The grammar is
small and the renderer is pure: one invocation cannot render the same token two ways.

## Grammar

| Token | Reads | Example |
| --- | --- | --- |
| `{path.to.field}` | a configuration value | `{experiment.name}` |
| `{path.to.field:function}` | that value, transformed | `{runtime.config_hash:coolname}` |
| `{:function}` | the invocation, not a value | `{:timestamp}` |

Rendered components are sanitized for every platform's filesystem, so a value
containing a slash or a reserved name cannot produce a surprising directory. When
sanitization changes something, Honbu warns rather than failing — it is surprising,
not wrong. Path-marked sources are exempt, since their values are whole paths and
sanitizing one would strip its leading separator.

## Built-in functions

| Function | Takes a value | Produces |
| --- | --- | --- |
| `timestamp` | no | local invocation time |
| `timestamp_utc` | no | UTC invocation time |
| `coolname` | optional | `{:coolname}` mints a fresh name per invocation; `{path:coolname}` mints a deterministic name for a digest |
| `slug` | yes | a filesystem-safe form of the value |
| `command` | no | the invoked command's name |
| `source_run_dir` | no | the run named by `--resolved` or `--fork-from` |

`source_run_dir` emits a whole path and so skips component sanitization.

## Your own functions

```python
from honbu.core.templates import BUILTIN_FUNCTIONS, TemplateFunctionSpec, TemplateRegistry
from honbu.flow import ProjectSpec

registry = TemplateRegistry([
    *BUILTIN_FUNCTIONS,
    TemplateFunctionSpec("fold", lambda ctx, value: f"fold{value:02d}"),
])
PROJECT = ProjectSpec(root_type=RootConfig, compose_spec=CONFIG, hashes=HASHES, templates=registry)
```

The specs you pass **replace** the built-ins rather than adding to them, so splat
`BUILTIN_FUNCTIONS` unless dropping `slug` and `coolname` is what you meant. Pass
`allow_override=True` to redefine one deliberately.

A function receives the invocation context and the resolved value (`None` for the
`{:name}` form), and decides which it accepts. Pass `emits_path=True` if it returns a
whole path.

## What a template may not name

A target template may not read a **deferred** digest or anything derived from one.
The directory has to exist before the work that computes the digest can run, so the
name cannot depend on it. This is checked when commands are declared, not when the
template is rendered.

## Setting one from the command line

Braces are also the compositor's dict syntax, so the quotes must survive the shell
*and* reach the compositor:

```bash
yourproject train 'outputs.dir_template="{experiment.name}/{runtime.run_id}"'
```

Honbu's refusal for a bare brace names the exact quoting to use.
