# Command line

Honbu does not own your CLI parser. `honbu.flow` is completely CLI-free, while two
optional layers provide standard command-line behavior:

- **`honbu.cli`** — library-agnostic helpers (`validate_override_tokens`,
  `split_dash_rule`, `select_config_source`, `exit_on_refusal`, `emit_warnings`,
  `CONVENTIONAL_FLAGS`), importing no CLI library.
- **`honbu.cli.presets`** — opt-in Typer presets (`HYDRA_CONTEXT_SETTINGS`,
  `Annotated` option aliases, customizable option factories, and mountable command
  builders for materialization and comparison).

## Conventional options

`honbu.cli.CONVENTIONAL_FLAGS` and `honbu.cli.presets` use these standard spellings
(projects may rename any flag via the option factories):

| Option | Alias in `honbu.cli.presets` | Takes a value | Meaning |
| --- | --- | --- | --- |
| `-c`, `--config PATH` | `ConfigOpt` | yes | compose that file, backed by the search path, instead of the default root |
| `--resolved REF` | `ResolvedOpt` | yes | replay a prior run's resolved configuration |
| `--fork-from REF` | `ForkFromOpt` | yes | start a new run from a prior run's authored `config/composed.yaml` |
| `--fork-artifact KEY` | `ForkArtifactOpt` | yes | select which parent artifact key to record in `manifest.parent` |
| `--config-dir DIR` | `ConfigDirsOpt` | yes, repeatable | prepend a configuration directory |
| `--clobber` | `ClobberOpt` | no | permit clearing within the command's declared scope |
| `--resume` | `ResumeOpt` | no | continue an unfinished run |

Pass `context_settings=HYDRA_CONTEXT_SETTINGS` on Typer commands so Hydra overrides
(`key=value`, `+key=value`, `~key`) pass through `ctx.args`, then validate them with
`validate_override_tokens(ctx.args)` and resolve the single active source with
`select_config_source(...)`.

### Forgiving positional `[CONFIG]`

`resolve_positional_config(token, project=PROJECT, extra_dirs=...)` turns a
positional token such as `configA` or `studies/v1_baseline` into one file, pairing
naturally with the exact `-c`/`--config`. It looks in two places: the filesystem
(`<tok>`, or `<tok>.yaml` when omitted) and the first search-path directory holding
it (`--config-dir` overlays, `./configs`, packaged), skipping the search path for
absolute, `./`-anchored, or `..`-traversing paths. A local copy shadowing a
packaged default counts once. No match refuses with a hint about `key=value` and
`--flag` spellings; two distinct matches refuse listing both and asking for
`-c`/`--config`; a run directory refuses pointing at `--resolved` or `--fork-from`.

For non-Typer CLIs (Click, `argparse`, or manual argv handling), `split_dash_rule`
refuses unknown dash-prefixed tokens and partitions trailing dash-free tokens into
`positionals` and `overrides` (`DashRuleSplit`).

## Override grammar

A dash-free token is an override if it contains `=` or begins with `~`:

| Form | Meaning |
| --- | --- |
| `key=value` | override an existing key (fails if absent, under struct mode) |
| `+key=value` | add a key (fails if present) |
| `++key=value` | add or replace |
| `~key` or `~key=value` | delete |
| `group=option` | select a configuration group option |

### Two refusals worth knowing

**`epochs=10,20`** is the compositor's multi-value sweep syntax, one character from
`epochs=[10,20]`, which is a single list value. Honbu never runs compositor multirun,
so it refuses and points at the sweep block.

**`dir_template={a}/{b}`** fails to parse, because `{` opens a dict in the override
grammar and a template in Honbu's. Quote it so the quotes reach the compositor:

```bash
yourproject train 'outputs.dir_template="{experiment.name}/{runtime.run_id}"'
```

## Exit codes

When your command runs inside `with exit_on_refusal(prog=...):`:

| Code | Means |
| --- | --- |
| 0 | done |
| 1 | a refusal — one message on stderr, no traceback |
| 130 | interrupted (`KeyboardInterrupt`) |
| *(traceback)* | a defect, in Honbu or in your handler |
