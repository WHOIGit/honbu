# Command line

Honbu splits argv before any CLI library sees it, using each command's declared
options and their arity. Every legal dash token is either a framework option or one
the command declares, so the split is never ambiguous.

## Framework options

| Option | Takes a value | Meaning |
| --- | --- | --- |
| `--config PATH` | yes | compose that file instead of the search path |
| `--resolved REF` | yes | replay a prior run's resolved configuration |
| `--continue-from REF` | yes | start a new run from a prior run's authored form |
| `--config-dir DIR` | yes, repeatable | prepend a configuration directory |
| `--clobber` | no | permit clearing within the command's declared scope |
| `--resume` | no | continue an unfinished run |

A command only accepts the ones its declaration implies. Offering `--clobber` on a
command with no target is refused, and the generated help lists exactly what the
parser accepts — one derivation feeds both, so they cannot drift apart.

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

| Code | Means |
| --- | --- |
| 0 | done |
| 1 | a refusal — one message, no traceback |
| 2 | unknown command |
| 130 | interrupted |
| *(traceback)* | a defect, in Honbu or in your handler |
