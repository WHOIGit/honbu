# Configure a project

## Ship defaults with the code

Put committed defaults in an importable package, not a loose directory:

```text
your_project/
├── config_defaults/
│   ├── __init__.py
│   └── config.yaml
└── ...
```

```python
from honbu.flow import ComposeSpec, compose_config

CONFIG = ComposeSpec(packaged="your_project.config_defaults")
config = compose_config(CONFIG)
```

A fresh checkout runs with no setup step, and a `configs/` directory in the working
directory shadows the package group by group. Precedence is explicit → local →
packaged; see [Configuration](../concepts/configuration.md) for the details.

## Assemble a strict root

```python
--8<-- "examples/linefit/project.py:blocks"
```

**Keep the root flat.** A configuration group's package path and a hash
declaration's include path both address this namespace, and nesting your blocks
under a `params:` umbrella makes every one of those declarations longer without
making anything clearer.

Honbu supplies four requirable blocks — experiment, runtime, an output block and its
leaves — and `validate_root_contract` checks at import that they are present and are
the base type or a subclass. Everything else is yours.

## Mark the fields that behave specially

| Type / marker | Use on | Effect |
| --- | --- | --- |
| `PortablePath` | filesystem paths | absolute when resolved, relative when stored, excluded from digests |
| `DERIVED` | values Honbu writes | authoring one is refused |
| `FromHash("name")` | a digest-bound field | assigned from that declared hash |
| `TemplatedIdentity` | a name rendered from other fields | rendered during resolution, excluded from digests |
| `ResolvedString` / `ResolvedPath` | fields only resolution fills | refused if authored |
| `DIRECTIVE` | instructions to the framework | kept out of digests; see [sweeps](sweeps.md) |

These are not annotations for the reader. They drive the digest source, the
portable/absolute projection, and comparison. See [Retype paths](paths.md) for
converting an existing schema, and [Identity](../concepts/identity.md) for why each
exclusion exists.

## Configuration groups

```yaml
# config_defaults/config.yaml
defaults:
  - _self_
  - dataset: reef
  - sweep: null
```

```bash
yourproject train dataset=kelp
```

A group is a directory of alternatives under the defaults package
(`config_defaults/dataset/reef.yaml`). Listing a group as `null` declares it without
selecting one — composing produces no block at all, and a later `group=option` needs
no `+` prefix.

## Author a value that is a template

Templates use `{path.to.field}` with optional functions:

```yaml
outputs:
  dir_template: "{experiment.name:slug}/{runtime.run_id}"
```

See the [template reference](../reference/templates.md) for the grammar and the
available functions. To set one from the command line, quote it so the braces reach
the compositor rather than only the shell:

```bash
yourproject train 'outputs.dir_template="{experiment.name}/{runtime.config_id}"'
```

## Check it

```python
def test_the_packaged_defaults_validate():
    from honbu.flow import compose_config
    RootConfig.model_validate(compose_config(CONFIG))
```

One test that composes the shipped defaults and validates them catches the whole
class of "the default configuration does not match the schema any more", which is
otherwise discovered by a user.
