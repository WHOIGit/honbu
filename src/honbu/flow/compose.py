"""Composition: configuration directories and overrides in, a plain dict out.

This is the half of :mod:`honbu.flow` that knows about files. It ends at a plain
dict; everything downstream — validation, identities, templates, paths — belongs
to the resolver, which never reads a configuration file. CLI token routing lives
in opt-in :mod:`honbu.cli`, never here (D19).

Hydra is a core dependency (D20), so it is imported at module scope like anything
else. Honbu makes the Hydra call because ``config_loader/compositor.py`` is one of
the modules that drifted in both sibling projects, and the drift-prone part is
precisely the part centralized here: assembling ``hydra.searchpath`` with a
defined precedence. :func:`build_search_path` is exported so a project needing
structured configs or custom Hydra features drives Hydra itself rather than
fighting a wrapper.

There is deliberately no named-experiment root source. ``experiment=reef-v3`` is
not special to Hydra — it works because experiment files carry a
``# @package _global_`` header, a convention any group may adopt. So
``data=nes-hf`` and ``training.epochs=50`` are the same kind of token, and the
model is a default root plus overrides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hydra import compose, initialize_config_dir
from hydra.errors import HydraException
from omegaconf import OmegaConf, open_dict

from honbu.errors import RefusalError

#: Where a configuration comes from. Mutually exclusive; overrides ride on top of
#: any of them. ``default`` is the project's config name under the search path.
ConfigSource = Literal["default", "file", "resolved", "fork"]

ALL_CONFIG_SOURCES: tuple[ConfigSource, ...] = ("default", "file", "resolved", "fork")


@dataclass(frozen=True)
class ComposeSpec:
    """Where a project's configuration lives.

    ``packaged`` is an importable package holding committed defaults; ``local`` is
    a directory relative to the working directory that shadows them (or ``None``
    for packaged-only composition). Precedence runs explicit → local → packaged,
    so a checkout can override a shipped default without editing it.
    """

    packaged: str | None = None
    local: str | None = "configs"
    config_name: str = "config"


@dataclass(frozen=True)
class Composition:
    """A composed mapping plus Hydra's recorded group selections."""

    config: dict[str, Any]
    choices: Mapping[str, str]


def build_search_path(
    spec: ComposeSpec,
    extra_dirs: Sequence[Path | str] = (),
    *,
    cwd: Path | None = None,
) -> tuple[Path, ...]:
    """Assemble the configuration search path, highest precedence first.

    Exported rather than kept private: this is the assembly both projects wrote
    separately, and a project driving Hydra itself needs exactly this ordering to
    stay consistent with one that does not.
    """
    found = search_dirs(spec, extra_dirs, cwd=cwd)
    if not found:
        if spec.local is not None:
            raise RefusalError(
                f"no configuration directory found: neither ./{spec.local} nor packaged "
                "defaults are available"
            )
        raise RefusalError(
            "no configuration directory found: no local directory configured and no "
            "packaged defaults are available"
        )
    return found


def search_dirs(
    spec: ComposeSpec,
    extra_dirs: Sequence[Path | str] = (),
    *,
    cwd: Path | None = None,
) -> tuple[Path, ...]:
    """Like :func:`build_search_path`, but an empty result is not a refusal.

    A nonexistent explicit ``extra_dirs`` entry still refuses; only the absence of
    a local tree and packaged defaults is tolerated. File roots and positional
    config lookup use this because their primary directory comes from elsewhere.
    """
    base = (cwd or Path.cwd()).resolve()
    found: list[Path] = []

    for entry in extra_dirs:
        candidate = Path(entry)
        candidate = candidate if candidate.is_absolute() else base / candidate
        if not candidate.is_dir():
            raise RefusalError(f"configuration directory does not exist: {candidate}")
        found.append(candidate.resolve())

    if spec.local is not None:
        local = base / spec.local
        if local.is_dir():
            found.append(local.resolve())

    if spec.packaged is not None:
        found.append(_packaged_dir(spec.packaged))
    return tuple(dict.fromkeys(found))


def compose_file(
    path: Path | str,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    compose_spec: ComposeSpec | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Compose an arbitrary configuration file, with overrides.

    Uses the file's own directory as the primary search directory and its stem as
    the config name, so the compositor applies overrides to it exactly as it would
    to the default root. That is what lets authored files and prior run bundles
    compose with overrides identically to the default root rather than using a
    weaker second override mechanism.

    With ``compose_spec``, the project's search path falls in behind the file's
    directory — ``[target.parent, *extra_dirs, local, packaged]`` — so a
    standalone authored root can select packaged groups that were never
    materialized locally. Without it the file is self-contained, which is what
    prior-run snapshots (``resolved`` and ``fork``) want.
    """
    return compose_file_with_choices(
        path,
        overrides=overrides,
        extra_dirs=extra_dirs,
        compose_spec=compose_spec,
        cwd=cwd,
    ).config


def compose_file_with_choices(
    path: Path | str,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    compose_spec: ComposeSpec | None = None,
    cwd: Path | None = None,
) -> Composition:
    """:func:`compose_file`, retaining the compositor's selected group options."""
    target = Path(path).resolve()
    if not target.is_file():
        raise RefusalError(f"configuration file does not exist: {target}")
    fallback = (
        search_dirs(compose_spec, extra_dirs, cwd=cwd)
        if compose_spec is not None
        else search_dirs(
            ComposeSpec(packaged=None, local=None), extra_dirs, cwd=cwd
        )
    )
    # The file's own directory leads so it is always the primary, even when a
    # fallback directory happens to hold a file of the same name.
    ordered = tuple(dict.fromkeys((target.parent, *fallback)))
    return compose_with_choices(
        ComposeSpec(packaged=None, local=None, config_name=target.stem),
        overrides=overrides,
        extra_dirs=ordered,
        cwd=cwd,
    )


def prior_config_path(
    reference: Path | str, filename: str, *, index: int = 1
) -> tuple[Path, Path]:
    """Locate a named configuration in a prior run, returning it and the run directory.

    Accepting a directory folds "point me at that run" into the existing source
    rather than adding another option. Index 1 is the default because it is the
    configuration the run was produced under; later indices belong to augmenting
    invocations that came after.
    """
    from honbu.flow.run import CONFIG_DIRNAME, config_dir_for

    # Run directories become output targets during augmenting resolution. Keep
    # them absolute so they can be relativized to an absolute output root exactly
    # once; a relative reference otherwise becomes ``runs/runs/...``.
    candidate = Path(reference).resolve()
    if candidate.is_file():
        # <run>/config/<file> or <run>/config/<N>/<file>
        holder = candidate.parent
        run_dir = holder.parent.parent if holder.name.isdigit() else holder.parent
        return candidate, run_dir
    if candidate.is_dir():
        located = config_dir_for(candidate, index) / filename
        if located.is_file():
            return located, candidate
        raise RefusalError(
            f"{candidate} holds no {CONFIG_DIRNAME}/{filename}; it is not a run "
            "directory Honbu produced"
        )
    raise RefusalError(f"no such run or configuration: {candidate}")


def resolved_config_path(reference: Path | str, *, index: int = 1) -> Path:
    """A prior run's *resolved* configuration — what a replay lands on."""
    from honbu.flow.run import RESOLVED_NAME

    return prior_config_path(reference, RESOLVED_NAME, index=index)[0]


def composed_config_path(reference: Path | str, *, index: int = 1) -> Path:
    """A prior run's *authored* configuration — what a fork builds on.

    Forking reads this rather than the resolved form because resolved input
    paths are absolute: ``./datasets/coco8`` is stored as
    ``/home/you/datasets/coco8``, which does not exist on the cluster node you
    meant to fork on. The authored form re-absolutizes wherever it runs.

    The cost, accepted: this holds only what was authored, so schema defaults are
    re-applied at fork time and a default that moved since the parent ran
    is silently picked up.
    """
    from honbu.flow.run import COMPOSED_NAME

    return prior_config_path(reference, COMPOSED_NAME, index=index)[0]


def _packaged_dir(package: str) -> Path:
    from importlib.resources import files

    try:
        located = files(package)
    except ModuleNotFoundError:
        raise RefusalError(f"packaged configuration {package!r} is not importable") from None
    path = Path(str(located))
    if not path.is_dir():
        raise RefusalError(f"packaged configuration {package!r} is not a directory")
    return path


#: Where sweep axes are declared, named in the refusal below so the message points
#: somewhere useful. Honbu owns expansion and never runs compositor multirun (D22).
SWEEP_BLOCK = "sweep"


def _parse_refusal(token: str, exc: Exception) -> str:
    """The compositor's grammar error, plus the fix when Honbu knows it.

    Braces are the one systematic collision between the two grammars a caller types
    into at once: they open a template in Honbu's and a dict in the compositor's. So
    every templated field — an output directory, a sweep's location — is unsettable
    from the command line without quotes that survive *the shell as well*, and the
    compositor's own message says only "mismatched input '{'", which describes the
    parse and not the remedy.
    """
    message = f"cannot parse override {token!r}: {exc}"
    key, separator, value = token.partition("=")
    if not separator or "{" not in value:
        return message
    return (
        f"{message}\n"
        f"The value contains a Honbu template, whose braces the compositor's grammar "
        f"reads as a dict. Quote it so the quotes reach the compositor rather than "
        f"only the shell:\n"
        f"  {key}='\"{value}\"'"
    )


def check_overrides(overrides: Sequence[str]) -> None:
    """Refuse a bare top-level comma, and translate a malformed override.

    ``epochs=10,20`` is a multi-value sweep in the compositor's grammar and one
    character from ``epochs=[10,20]``, a single list value — opposite meanings.
    Honbu does not run compositor multirun, so accepting it would either discard
    every value but one or smuggle in an execution model it does not own.

    Left to the compositor this surfaced as an uncaught exception whose advice was
    to add ``--multirun``, which is precisely the thing that will not work here.
    """
    from hydra.core.override_parser.overrides_parser import OverridesParser

    parser = OverridesParser.create()
    for token in overrides:
        try:
            parsed = parser.parse_overrides([token])[0]
        except Exception as exc:  # the compositor's own grammar errors
            raise RefusalError(_parse_refusal(token, exc)) from None
        if "SWEEP" not in str(parsed.value_type or ""):
            continue
        key = parsed.get_key_element()
        raise RefusalError(
            f"{token!r} reads as a multi-value sweep. Honbu expands sweeps itself and "
            f"never runs compositor multirun, so declare the axis in the "
            f"{SWEEP_BLOCK!r} block instead:\n"
            f"  {SWEEP_BLOCK}.axes.{key}=[...]\n"
            f"To set a single list value, bracket it: {key}=[...]. "
            f"To set a string containing a comma, quote it: {key}='a,b'."
        )


#: The two open containers whose non-emptiness makes a sweep block *active*. A
#: block carrying only a gate is inert: projects set a default gate in the base
#: configuration so a sweep declared entirely in overrides inherits one, and if a
#: gate alone counted as a sweep that default would refuse every ordinary run.
SWEEP_AXIS_CONTAINERS = ("axes", "covariant_axes")


def sweep_is_active(composed: Mapping[str, Any]) -> bool:
    """Whether a composed configuration asks for a sweep.

    Deliberately structural rather than typed. The sweep capability owns the block's
    schema and lives a layer above this one, so asking the question properly here
    would invert the dependency for a dict lookup. What flow needs to know is only
    whether to hand off, and that is visible without the schema.
    """
    block = composed.get(SWEEP_BLOCK)
    if not isinstance(block, Mapping):
        return False
    return any(bool(block.get(name)) for name in SWEEP_AXIS_CONTAINERS)


def refuse_active_sweep_block(composed: Mapping[str, Any], *, invocation: str) -> None:
    """Refuse to run a single command against a configuration describing many.

    One command, one target. A configuration with two axes describes twelve runs;
    running it would produce exactly one, from the base values — an arbitrary point
    of the matrix, with a run directory, a manifest and metrics all looking like a
    sweep that never happened. Erroring costs one command, ignoring costs the
    experiment and says nothing.
    """
    if not sweep_is_active(composed):
        return
    raise RefusalError(
        f"{invocation} was given an active {SWEEP_BLOCK!r} block, which describes "
        f"more than one run. A command has one target, so this would silently "
        f"produce a single child from the base values. Prepare the sweep instead, "
        f"then run its children."
    )


def force_add_sweep_overrides(overrides: Sequence[str]) -> list[str]:
    """Rewrite a bare ``sweep.*`` override to the compositor's force-add form.

    The sweep block is an *open* mapping: its axis keys are configuration paths the
    caller chooses, so they cannot be pre-declared in the authored YAML the way an
    ordinary field is. The compositor composes in struct mode, where assigning a key
    that does not already exist is an error advising a ``+`` prefix — which would
    make ``sweep.axes.epochs=[10,20]`` fail for the sole reason that no previous
    sweep happened to use ``epochs``, and would make the prefix depend on the
    authored file rather than on what the caller meant.

    Since every key under the block is open by construction, the prefix carries no
    information and Honbu supplies it. ``++`` rather than ``+`` because force-add
    also *replaces*, so redeclaring an axis the base configuration already sets
    behaves like any other override instead of failing as a duplicate.

    An explicitly prefixed token is left alone: someone writing ``~sweep.axes.epochs``
    to delete an axis means it.
    """
    prefix = f"{SWEEP_BLOCK}."
    return [
        f"++{token}" if token.startswith(prefix) else token for token in overrides
    ]


def compose_config(
    spec: ComposeSpec,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Compose the default root into a plain dict."""
    return compose_with_choices(
        spec, overrides=overrides, extra_dirs=extra_dirs, cwd=cwd
    ).config


def compose_with_choices(
    spec: ComposeSpec,
    *,
    overrides: Sequence[str] = (),
    extra_dirs: Sequence[Path | str] = (),
    cwd: Path | None = None,
) -> Composition:
    """Compose and retain the compositor's selected group options.

    Materialization consumes these choices rather than re-implementing Hydra's
    defaults-list resolution.
    """
    check_overrides(overrides)
    overrides = force_add_sweep_overrides(overrides)
    search = build_search_path(spec, extra_dirs, cwd=cwd)

    primary = next(
        (path for path in search if (path / f"{spec.config_name}.yaml").is_file()), None
    )
    if primary is None:
        listed = "\n  ".join(str(path) for path in search)
        raise RefusalError(
            f"no {spec.config_name}.yaml on the configuration search path:\n  {listed}"
        )

    # The primary directory outranks every hydra.searchpath entry for groups of the
    # same name — verified against Hydra, not assumed. So the directory holding the
    # primary config is the highest-precedence one, and a --config-dir overlay
    # shadows only groups that directory lacks. An overlay wanting to win outright
    # ships its own config.yaml, which makes it the primary.
    searchpath = [str(path) for path in search if path != primary]
    full = list(overrides)
    if searchpath:
        full.insert(0, f"hydra.searchpath=[{','.join(searchpath)}]")

    try:
        with initialize_config_dir(config_dir=str(primary), version_base=None):
            composed = compose(
                config_name=spec.config_name, overrides=full, return_hydra_config=True
            )
    except HydraException as exc:
        # A composition error is a message to the user about their configuration,
        # not a defect in Honbu, so it exits like one rather than as a traceback.
        raise RefusalError(f"composition failed: {exc}") from None
    # Hydra's own tree contains deliberately unresolved sweep fields (for example
    # ``hydra.job.num``). Read just its choices without resolving them, then drop
    # the entire internal tree before resolving the project configuration.
    choices_node = OmegaConf.select(
        composed, "hydra.runtime.choices", throw_on_resolution_failure=False
    )
    raw_choices = OmegaConf.to_container(choices_node, resolve=False) if choices_node else {}
    with open_dict(composed):
        del composed["hydra"]
    container = OmegaConf.to_container(composed, resolve=True)
    if not isinstance(container, dict):
        raise RefusalError("composed configuration is not a mapping")
    return Composition(
        config=container,
        choices={
            str(group): str(option)
            for group, option in (raw_choices.items() if isinstance(raw_choices, dict) else ())
        },
    )
