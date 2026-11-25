# Configuration

Configuration reaches your handler in two acts. It is **composed** — files and
overrides merged into a plain mapping — and then **resolved** — validated into your
strict model, given identities, and placed somewhere on disk. The two are separate
because only the first is portable, and only the second can be run.

## Composition

Hydra composes. Honbu owns the search path, the precedence, and the argv grammar
around it, because that is the assembly two projects wrote separately and
inconsistently.

Precedence runs **explicit → local → packaged**:

| Source | Where | Beats |
| --- | --- | --- |
| `--config-dir` | wherever you point it | local, packaged |
| `./configs` | the working directory | packaged |
| `your_project.config_defaults` | shipped with the code | — |

So a checkout can override a shipped default without editing it, and a shipped
default always exists, so a fresh clone runs.

### Overrides

An argv token that is dash-free and either contains `=` or begins with `~` is an
override, handed to the compositor as written:

| Form | Meaning |
| --- | --- |
| `key=value` | override an existing key; fails if it does not exist |
| `+key=value` | add a key; fails if it exists |
| `++key=value` | add or replace, always |
| `~key` | delete the key |
| `group=option` | select a configuration group's option |

Composition runs in **struct mode**, which is why plain `key=value` fails on a key
your schema does not declare. That is deliberate: a typo'd override is an error
rather than a no-op, which is the whole reason the schema is strict.

!!! warning "Braces need quotes that survive the shell"

    Honbu's templates use `{...}`, which the compositor's override grammar reads as
    a dict. Setting a template from the command line needs the quotes to reach the
    compositor, not just the shell:

    ```bash
    yourproject train 'outputs.dir_template="{experiment.name}/{runtime.run_id}"'
    ```

    Honbu's refusal for this case names the exact quoting to use.

## Composed vs. resolved, and the two resolved views

A run persists two configuration files before your handler is called, so even a run
that dies in its first minute records what it was going to do:

- **`config/composed.yaml`** — what you authored before resolution, with relative
  input paths and unrendered templates intact. `--fork-from` builds on this file so a
  forked run re-resolves cleanly on another machine.
- **`config/resolved.yaml`** — the **portable resolved view** (`resolution.portable`):
  every identity and digest is filled in, while bare-relative output directories and
  leaves stay relative so moving a `runs/` tree keeps working. `--resolved` replays
  this file.

In memory, your handler reads **`run.config`** — the **absolute resolved view**
(`resolution.config`), of the same Pydantic type, with every output path joined to
`output_root` so your code can open paths directly without manual joining. Both views
produce identical digests.

## The resolution order is fixed

Resolution is a sequence of phases, in one order, always:

1. **Validate** the composed mapping into the strict root.
2. **Anchor paths** — every `PORTABLE_PATH` field becomes absolute.
3. **Compute digests** that can be known now; mark the rest deferred.
4. **Assign** digest-bound fields (`FromHash`).
5. **Render identities** — templated fields like `{runtime.config_hash:coolname}`.
6. **Render the target template** and anchor it under the output root.
7. **Check the target** for collisions and containment.
8. **Project the two views**.

The order is not an implementation detail; it is the reason each step can be
written honestly. A digest cannot cover a path that has not been anchored yet, and
an identity cannot be rendered from a digest that has not been computed. A project
that resolved these in its own order would get a digest that changes with the
working directory — which is exactly the drift that made two sibling projects
incomparable.

## Configuration groups

A group is a directory of alternatives; selecting one is an ordinary override.

```yaml
# config.yaml
defaults:
  - _self_
  - dataset: reef          # -> config_defaults/dataset/reef.yaml
  - sweep: null            # declared, none selected
```

```bash
yourproject train dataset=kelp        # swap the whole block
yourproject train sweep=resolution    # select a sweep definition
```

Listing a group with `null` is how you declare that it exists without choosing one:
composing without a selection produces no block at all, and selecting one later
needs no `+` prefix.

Honbu records the compositor's choices and uses them to
[materialize a starter tree](../how-to/materialize.md) — it never re-implements the
defaults-list algorithm.

---

Next: [Identity](identity.md) — what those digests actually cover.
