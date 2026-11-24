# Why Honbu exists

Two sibling projects trained models. They were written months apart, by the same
person, for the same lab. Each had a configuration schema, a run directory, a
manifest, a resume flag, and a sweep. None of it was shared — the second project
was the first one copied and adapted.

That is the failure Honbu is built against, and it is worth being precise about
what makes it a failure. It is not duplication; duplication is cheap. It is that
the two copies **drifted**, and nothing could tell you where. One project's run
directory included the dataset in its digest and the other's did not, so a number
from one was not comparable to a number from the other, and nothing anywhere said
so. The bug is invisible by construction: both programs run, both produce results,
and the results are quietly incomparable.

Honbu's answer is not a base class. It is this:

## Everything is declared once

A `CommandSpec` declares its mode, target, steps, hashes, accepted configuration
sources, and write scope. A `ProjectSpec` bundles the root type, composition search
path, and hash registry. From those declarations Honbu drives configuration loading,
resolution order, collision checks, and the blast radius of `--clobber` — with no
CLI framework coupled into `honbu.flow`.

Your project owns its own command-line parser (Typer, Click, `argparse`, or plain
Python calls). When you want standard flags and override handling without
boilerplate, `honbu.cli` provides library-agnostic helpers and `honbu.cli.presets`
provides opt-in Typer option aliases and mountable commands.

## Identity is a property of the configuration, not of the clock

A run's digest covers what the configuration *means*, not what it happens to say on
this machine. Absolute paths, generated values, template strings, and framework
directives are excluded by their markers — not by a convention, and not by a filter
somebody remembered to write, but by the type of the field. Two people on two
machines with the same intent get the same digest.

That is what makes results comparable across time and hardware, which is the whole
point of writing a digest down.

## A refusal is cheaper than a wrong answer

Honbu refuses a lot: a sweep block handed to a single command, a gate that cannot
tell two children apart, an output template that would put two runs in one
directory, a value authored into a field the framework writes.

Every one of those refusals replaces a silent wrong answer. Running a four-axis
sweep as a single run *works* — it produces a run directory, a manifest, and
metrics, all of them describing an arbitrary corner of the matrix nobody asked for.
The refusal costs one command. The silence costs the experiment, and says nothing.

A refusal names the fix rather than the rule. `epochs=10,20` does not say "invalid
override"; it says that is a multi-value sweep, that Honbu expands sweeps itself,
and that the axis belongs in the sweep block — with the line to type.

## Provenance is written before the work, not after

The configuration bundle, the digests and the target are all committed before your
handler is called. A run that dies in its first minute still says exactly what it
was going to do. Recording provenance afterwards means the runs that most need
explaining — the ones that crashed — are the ones with nothing written down.

## You keep the main loop

Honbu never inverts control. Your project calls it; it does not call you. There is
no `Trainer` to subclass, no CLI dispatcher to yield control to, and no lifecycle
hook to fill in. Your own CLI callback calls `load_config`, `resolve`, and
`open_run` directly, or invokes them from a notebook or script with no CLI at all.

The layers make that concrete: core, flow, and operations depend one way only, and
a project that wants identity and provenance without commands imports only the
first. Opting out of a layer means not importing it.

---

Next: [Configuration](configuration.md) · [Identity](identity.md) ·
[Runs](runs.md) · [Refusals](refusals.md)
