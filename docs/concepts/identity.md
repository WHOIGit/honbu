# Identity

A digest turns a configuration into a name. The question that decides whether the
name is worth anything is: **what does it cover?**

Get it wrong in one direction and every run is unique, so nothing is ever
recognized as a repeat. Get it wrong in the other and two genuinely different runs
share a name, so one silently overwrites the other. Both failures are quiet.

## What a digest covers

Honbu's answer is the **behavioral** configuration: what the configuration means,
not what it happens to say on this machine at this moment. Four kinds of field are
excluded, each by a marker on its type rather than by a filter someone remembered
to write.

| Marker | Excludes | Because |
| --- | --- | --- |
| `PORTABLE_PATH` | filesystem paths | `/home/ana/data` and `/scratch/data` are the same dataset. Including them would make a colleague's identical experiment a different one. |
| `DERIVED` | values Honbu generates | A digest cannot cover a field whose value is the digest. |
| `TEMPLATED` | template-valued identities | The rendered name is a *label for* the configuration, not an input to it. |
| `DIRECTIVE` | instructions to the framework | A sweep block tells Honbu what to do, not the run what to compute. A prepared child's identity must equal that of the same configuration run by hand. |

The markers are not documentation. They are read by the walkers that build the
digest source, and by comparison, and by the portable/absolute projection. Forget
one and everything still works — only the digests are wrong, silently — which is
why `validate_root_contract` and the `FromHash` binding check them at import.

## Identity is not the same as digest

A **digest** is a hash: `sha256:1f3a...`. It is what comparison gates on.

An **identity** is a name minted from a digest for people and directories:
`dynamic-macaw`. It is what appears in paths.

```python
config_hash: Annotated[ResolvedString, DERIVED, FromHash("config_hash")] = None
config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"
```

They are separate because they answer to different pressures. A digest must be
stable and total; an identity must be short, filesystem-safe, and human-sayable, and
those goals are lossy. Collapsing them means either unreadable directories or a
weakened digest.

Honbu ships two identities on the runtime block:

`runtime.run_id`
:   Fresh per invocation. Two runs of one configuration coexist.

`runtime.config_id`
:   Derived from the configuration digest. The same configuration is the same name,
    forever.

**Which one belongs in your output template is the most consequential line in your
schema.** `{runtime.run_id}` gives you repeat runs that accumulate — the usual
choice for training, where you often want to run the same thing twice. Point it at
`{runtime.config_id}` and one configuration owns one directory: repeats resume
rather than accumulate, and content addressing is real. Sweeps require the second
kind, because a child that cannot be found again cannot be resumed or grown.

## The four hash forms

| Form | Covers | Deferred |
| --- | --- | --- |
| `FieldHash` | named subtrees of the configuration | no |
| `SourceHash` | a mapping you build, optionally from inputs | when `reads_inputs=True` |
| `ByteHash` | bytes you supply | no |
| `FannedHash` | one entry per key of a mapping — a digest per split | inherits |

```python
--8<-- "examples/linefit/project.py:hashes"
```

## Deferred digests

A digest over the *inputs* cannot be computed before the run opens — the files have
to be read, and reading them is work. Declaring `reads_inputs=True` makes that hash
deferred: it is not computed during resolution, the deferred set is recorded, and
the run completes it where the inputs are first read.

```python
run.complete_hash("dataset_hash", inputs={"points": len(points)})
```

Deferral is **declared, not discovered**, so the deferred set is static and
identical on every machine. A target template may not name a deferred value, for the
obvious reason: the directory has to exist before the work that computes the digest
can run.

## What this buys

Because identity is behavioral, a run's digest answers a question that survives
being moved: *is this the same experiment?* Two machines, two checkouts, two people
— same intent, same digest. That is what makes a comparison meaningful and a cached
result trustworthy, and it is the thing two hand-written sibling projects could not
give each other.

---

Next: [Runs](runs.md) — what happens once identity is settled.
