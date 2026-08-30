# Compare runs

Two runs disagreed. This tells you where.

```python
from honbu.compare import ConfigOperand, compare_operands, render_comparison

comparison = compare_operands(
    [
        ConfigOperand(source="resolved", reference="runs/reef/dynamic-macaw"),
        ConfigOperand(source="resolved", reference="runs/reef/polite-cricket"),
    ],
    compose_spec=CONFIG,
    level="behavioral",
    root_type=RootConfig,
    hashes=HASHES,
)
print(render_comparison(comparison))
```

An operand is any configuration you can name: the packaged default, a file, a prior
run's resolved or authored form, each with its own overrides on top. Comparison is
N-way, so three runs and a baseline is one call.

## Choose the level deliberately

| Level | Compares | Answers |
| --- | --- | --- |
| `literal` | every key as written | "what differs in these files?" |
| `authored` | what a person wrote, ignoring resolution | "did we ask for different things?" |
| `behavioral` | the digest projection — markers excluded | "are these the same experiment?" |
| `by_hash` | declared digests only | "do these compare at all?" |

`behavioral` is usually the one you want. It ignores the differences that cannot
change a result — the run's own name, the machine's paths, rendered identities — and
shows the ones that can. A `literal` diff of two runs from different machines is
mostly noise about `/home` versus `/scratch`.

## Where it belongs

Comparison is a library, not a command. Honbu does not register a `compare` command
for you, because the useful surface is project-shaped: which operands are named
positionally, what the default level is, whether the output is a table or JSON. Build
`ConfigOperand` values from your own CLI and render with `render_comparison` or your
own renderer over `comparison.differences`.
