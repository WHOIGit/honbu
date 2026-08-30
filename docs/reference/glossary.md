# Glossary

The terms an adopter meets, used precisely. Where two projects used one word for two
things, that is recorded rather than resolved by fiat.

## Configuration

**Composed configuration**
:   The plain mapping the compositor produces from authored files and overrides,
    before Honbu touches it. Holds only what was authored. Persisted as
    `config/composed.yaml`.

**Resolved configuration**
:   The composed configuration after the resolution phases: paths anchored, digests
    assigned, identities rendered, target resolved, revalidated. Persisted as
    `config/resolved.yaml`.

**Portable view / absolute view**
:   Two forms of one resolved configuration. The portable view keeps relative output
    paths relative; the absolute view has every path joined. Their digests are equal,
    because paths never enter a digest.

**Configuration group**
:   A directory of interchangeable option files, one selected per composition.
    Selection is an ordinary override (`dataset=kelp`), never a distinct source.

**Config source**
:   One of four mutually exclusive origins: the default config, an authored file, a
    prior run's resolved configuration, or a parent run to continue from.

**Replay**
:   Reusing a prior run's *resolved* configuration, which carries a literal output
    directory and therefore lands in that same run.

**Continue**
:   Reusing a prior run's *composed* configuration, producing a new run with recorded
    lineage. Survives being moved to another machine; a replay does not.

## Identity

**Source**
:   The mapping a hash is taken over, carrying a `version`. Persisted beside its
    digest, so what was hashed is recoverable.

**Digest**
:   The hash of a source. What comparison gates on.

**Identity**
:   A human-readable name minted from a digest — `dynamic-macaw`. What appears in
    paths. Lossy by design, and therefore not the digest.

**Deferred digest**
:   A digest that cannot be computed before the run opens because computing it reads
    inputs. Declared, not discovered, so the deferred set is the same everywhere.

**Behavioral configuration**
:   What remains after the excluded markers are projected away: what the
    configuration means rather than what it says on this machine.

## Runs

**Run**
:   A directory containing everything true about one execution: provenance,
    manifest, digests, artifacts.

**Producing command**
:   A command that creates a run.

**Augmenting command**
:   A command that adds to a run that exists, confined to a declared scope.

**Step**
:   A declared unit of work that is not repeated once completed. The granularity of
    resume.

**Scope**
:   The output leaves a command may replace. The declaration supplies the blast
    radius; `--clobber` supplies only permission.

**Artifact**
:   A file registered by name in the manifest, so a later command can find it without
    knowing the layout.

**Refusal**
:   A message to the user about their configuration or invocation. Exits non-zero
    with no traceback, as distinct from a defect, whose traceback survives.

## Sweeps

**Sweep**
:   A set of prepared child runs expanded from one configuration. Not a multirun mode:
    each child is an ordinary run.

**Axis**
:   One dimension of the expansion. A *path* axis assigns a configuration path; a
    *group* axis selects whole option files; a *covariant* axis assigns several paths
    at once. Every axis contributes exactly one dimension.

**Covariance**
:   Parameters that must move together. A diagonal, not a grid.

**Gate**
:   The configuration path whose value distinguishes children, and which must appear
    in the child output template.

**Child**
:   One run of a sweep. Its digests equal those of the same configuration invoked by
    hand, which is what makes it comparable to a baseline.

**Preparation**
:   Expanding, resolving and checking every child, then writing them all in the
    `initialized` state — before any work runs.

**Sweep identity**
:   Derived from the sweep's *directory*, never from its definition, so that adding an
    axis value extends the sweep instead of naming a new one.
