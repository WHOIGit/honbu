# Refusals

Honbu refuses things. This page is about why there are so many, and what shape they
take, so that meeting one feels like being told something rather than being blocked.

## A refusal is not a defect

Two failures leave Honbu, and they are deliberately different.

**A refusal** — `RefusalError` — is a message to you about your configuration or
your invocation. It prints, exits non-zero, and shows **no traceback**, because a
stack trace through a framework you did not write is noise when the problem is a
typo in an override.

**Anything else** is a bug, in Honbu or in your handler, and its traceback survives
intact. Swallowing it would trade a diagnosable crash for a mystery.

| Situation | Exit | Output |
| --- | --- | --- |
| Refusal | 1 | one message, no traceback |
| Defect | traceback | the full stack |
| Interrupt | 130 | `interrupted` |

Wrap your CLI entrypoint body in `with exit_on_refusal(prog="yourproject"):` from
`honbu.cli` to get all three behaviors automatically, with an optional `on_error`
hook if you want custom or structured output.

## A refusal names the fix

The rule Honbu holds itself to is that a message should say what to do, not which
invariant you tripped.

Compare what the compositor says on its own:

```text
mismatched input '{' expecting <EOF>
```

with what Honbu says:

```text
cannot parse override 'outputs.dir_template=./runs/{experiment.name}': mismatched input '{'
The value contains a Honbu template, whose braces the compositor's grammar reads as
a dict. Quote it so the quotes reach the compositor rather than only the shell:
  outputs.dir_template='"./runs/{experiment.name}"'
```

The first describes a parse. The second describes a fix. Both are true; only one
ends the problem.

More examples of the same discipline:

- `epochs=10,20` is valid compositor syntax for a multi-value sweep, one character
  from `epochs=[10,20]`, which is a single list. Honbu does not run compositor
  multirun, so it refuses — and the refusal names the sweep block, gives the line to
  type, and shows both alternatives.
- A sweep whose gate cannot separate two children names *the colliding children and
  the axes whose values differ between them*, because those are precisely the axes
  that failed to do their job.
- A project that hands Honbu a sweep block without declaring the field is told the
  one line to add to its root, rather than the strict schema's "extra inputs are not
  permitted", which names the rule and not the remedy.

## Why refuse instead of guard

Every refusal in Honbu replaces a silent wrong answer, not a crash.

A configuration with two axes describes twelve runs. Handing it to a single command
*works*: you get a run directory, a manifest, and metrics — for one arbitrary corner
of the matrix, chosen by which values happened to be the base ones. Nothing is
broken, nothing is logged, and the experiment is wrong. The refusal costs one
command; the silence costs the experiment and says nothing.

Two children resolving to one directory is the same shape. So is authoring a value
into a field Honbu writes: it would work, and only the digest would be wrong.

The general rule: **Honbu refuses where the alternative is invisible.** Where a
mistake announces itself — a missing file, a bad checkpoint — there is nothing to
add, and Honbu stays out of the way.

## Where it deliberately does not refuse

- **Metrics.** Honbu records what you record and interprets none of it. There is no
  check that your objective improved, no direction convention, no schema.
- **Your work.** Inside a step, Honbu has no opinion. Raise, and it is recorded as a
  failure with the traceback intact.
- **Warnings that are not errors.** A path component that had to be sanitized, an
  axis that displaces a base value, a case-insensitive directory collision: these
  are printed and the run proceeds, because they are surprising rather than wrong.

---

Back to [Why Honbu exists](index.md), or on to the how-to guides for
[commands](../how-to/commands.md) and [hashes](../how-to/hashes.md).
