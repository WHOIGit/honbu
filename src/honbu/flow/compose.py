"""Composition: argv and configuration directories in, a plain dict out.

This is the half of L2 that knows about files. It ends at a dict; everything
downstream — validation, identities, templates, paths — is the resolver's, which
never sees argv and never reads a configuration file. That boundary is what makes
the resolver usable on a dict you built in a notebook.

Hydra is a core dependency (D20), so it is imported at module scope like anything
else. It used to be imported lazily inside :func:`compose_config` to keep this
module importable without the ``flow`` extra; that extra is gone, and with it the
branch whose only job was to tell someone mid-command to go install something.

Honbu makes the Hydra call because ``config_loader/compositor.py`` is one of the
modules that drifted in both projects, and the drift-prone part is precisely the
part duplicated here: assembling ``hydra.searchpath`` with a defined precedence.
Owning the call prescribes much less than it looks like. A Compose call needs a
search path, a config name and overrides; group names, directory layout and
defaults lists all stay in the project's own YAML. :func:`build_search_path` is
exported so a project needing multirun or structured configs drives Hydra itself
rather than fighting a wrapper.

There is deliberately no named-experiment root source. ``experiment=reef-v3`` is
not special to Hydra — it works because experiment files carry a
``# @package _global_`` header, a convention any group may adopt. So
``data=nes-hf`` and ``training.epochs=50`` are the same kind of token, and the
model is a default root plus overrides.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hydra import compose, initialize_config_dir
from hydra.errors import HydraException
from omegaconf import OmegaConf

from honbu.errors import RefusalError

#: Where a configuration comes from. Mutually exclusive; overrides ride on top of
#: any of them. ``default`` is the project's config name under the search path.
ConfigSource = Literal["default", "file", "resolved", "continue"]

ALL_CONFIG_SOURCES: tuple[ConfigSource, ...] = ("default", "file", "resolved", "continue")


@dataclass(frozen=True)
class OptionSpec:
    """A dash-prefixed token the split must recognize.

    Arity has to be declared because the split runs before the CLI framework sees
    argv: given ``--config x.yaml``, only the option's arity says whether
    ``x.yaml`` is its value or a positional. Every legal dash token is either a
    framework option or declared by the command, so arity is always known and the
    ambiguity never arises.
    """

    name: str
    takes_value: bool = False
    repeatable: bool = False


#: Options Honbu itself consumes. Every one of them acts *before or around* project
#: code — which configuration is used, where it is looked for, and whether Honbu may
#: write where it intends. None of them decides how much of a command's work runs;
#: that is the project's, expressed by declaring a command (D25).
#:
#: Two absences by decision. ``--force`` never fired under a timestamped run
#: template and fired on every deliberate re-run under a deterministic one, so a
#: described occupant replaced it. ``--init`` meant "persist the bundle and stop",
#: which is the one thing here that decided how much work happened; with a partial
#: run state a project declares its own command for that and names it itself.
FRAMEWORK_OPTIONS: tuple[OptionSpec, ...] = (
    OptionSpec("--config", takes_value=True),
    OptionSpec("--resolved", takes_value=True),
    OptionSpec("--continue-from", takes_value=True),
    OptionSpec("--config-dir", takes_value=True, repeatable=True),
    OptionSpec("--clobber"),
    OptionSpec("--resume"),
)

#: Which framework option supplies which configuration source.
SOURCE_OPTIONS: Mapping[ConfigSource, str] = {
    "file": "--config",
    "resolved": "--resolved",
    "continue": "--continue-from",
}


@dataclass(frozen=True)
class ComposeSpec:
    """Where a project's configuration lives.

    ``packaged`` is an importable package holding committed defaults; ``local`` a
    directory relative to the working directory that shadows them. Precedence runs
    explicit → local → packaged, so a checkout can override a shipped default
    without editing it.
    """

    packaged: str | None = None
    local: str = "configs"
    config_name: str = "config"


@dataclass(frozen=True)
class ArgvSplit:
    overrides: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    positionals: tuple[str, ...] = ()


def split_argv(
    tokens: Sequence[str],
    *,
    options: Iterable[OptionSpec] = (),
    framework: Iterable[OptionSpec] = FRAMEWORK_OPTIONS,
    reason: Mapping[str, str] | None = None,
) -> ArgvSplit:
    """Route argv into overrides, options and positionals.

    A token containing ``=`` and no leading dash is an override, which handles the
    compositor's own ``+``, ``++`` and ``~`` prefixes for free — all are dash-free.

    ``framework`` is the subset of framework options this caller accepts; ``reason``
    explains, per excluded option, why it does not apply, so a refusal says more than
    "unknown option" for a token that is real but out of place.
    """
    known = {spec.name: spec for spec in (*framework, *options)}
    excluded = dict(reason or {})

    overrides: list[str] = []
    positionals: list[str] = []
    collected: dict[str, Any] = {}

    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1

        if token.startswith("-"):
            name, separator, inline = token.partition("=")
            spec = known.get(name)
            if spec is None:
                if name in excluded:
                    raise RefusalError(
                        f"{name} does not apply here: {excluded[name]}"
                    )
                raise RefusalError(
                    f"unknown option {name!r}; known options: "
                    f"{', '.join(sorted(known))}"
                )
            if not spec.takes_value:
                if separator:
                    raise RefusalError(f"{name} is a flag and takes no value")
                collected[spec.name] = True
                continue
            if separator:
                value = inline
            elif index < len(tokens):
                value = tokens[index]
                index += 1
            else:
                raise RefusalError(f"{name} requires a value")
            if spec.repeatable:
                collected.setdefault(spec.name, []).append(value)
            elif spec.name in collected:
                raise RefusalError(f"{name} given more than once")
            else:
                collected[spec.name] = value
            continue

        if "=" in token:
            overrides.append(token)
        else:
            positionals.append(token)

    return ArgvSplit(
        overrides=tuple(overrides),
        options=collected,
        positionals=tuple(positionals),
    )


def selected_source(
    options: Mapping[str, Any],
    *,
    accepted: Sequence[ConfigSource],
) -> tuple[ConfigSource | None, str | None]:
    """The single configuration source in play, refusing conflicts.

    Returns ``(None, None)`` for a command accepting no source, so a command that
    only reads a checkpoint is not broken by a missing default root.
    """
    if not accepted:
        offenders = [SOURCE_OPTIONS[s] for s in ALL_CONFIG_SOURCES[1:]]
        supplied = [name for name in offenders if name in options]
        if supplied:
            raise RefusalError(
                f"this command takes no configuration; remove {', '.join(supplied)}"
            )
        return None, None

    supplied = [
        (source, options[name])
        for source, name in SOURCE_OPTIONS.items()
        if name in options
    ]
    if len(supplied) > 1:
        names = ", ".join(SOURCE_OPTIONS[source] for source, _ in supplied)
        raise RefusalError(f"configuration sources are mutually exclusive: {names}")

    if not supplied:
        if "default" not in accepted:
            offered = ", ".join(
                SOURCE_OPTIONS[source] for source in accepted if source in SOURCE_OPTIONS
            )
            raise RefusalError(
                "this command cannot compose a fresh configuration; "
                f"identify an existing one with {offered}"
            )
        return "default", None

    source, value = supplied[0]
    if source not in accepted:
        offered = ", ".join(
            SOURCE_OPTIONS[candidate]
            for candidate in accepted
            if candidate in SOURCE_OPTIONS
        ) or "no source option"
        raise RefusalError(
            f"{SOURCE_OPTIONS[source]} is not accepted by this command; use {offered}"
        )
    return source, str(value)


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
    base = (cwd or Path.cwd()).resolve()
    found: list[Path] = []

    for entry in extra_dirs:
        candidate = Path(entry)
        candidate = candidate if candidate.is_absolute() else base / candidate
        if not candidate.is_dir():
            raise RefusalError(f"configuration directory does not exist: {candidate}")
        found.append(candidate.resolve())

    local = base / spec.local
    if local.is_dir():
        found.append(local)

    if spec.packaged is not None:
        found.append(_packaged_dir(spec.packaged))

    if not found:
        raise RefusalError(
            f"no configuration directory found: neither ./{spec.local} nor packaged "
            "defaults are available"
        )
    return tuple(found)


def compose_file(path: Path | str, *, overrides: Sequence[str] = ()) -> dict[str, Any]:
    """Compose an arbitrary configuration file, with overrides.

    Uses the file's own directory as the search path and its stem as the config
    name, so the compositor applies overrides to it exactly as it would to the
    default root. That is what lets ``--config my.yaml epochs=50`` and
    ``--resolved <run> epochs=50`` behave like every other invocation rather than
    being a second, weaker override mechanism.
    """
    target = Path(path)
    if not target.is_file():
        raise RefusalError(f"configuration file does not exist: {target}")
    spec = ComposeSpec(packaged=None, local=str(target.parent), config_name=target.stem)
    return compose_config(spec, overrides=overrides, cwd=target.parent.parent or Path("."))


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

    candidate = Path(reference)
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
    """A prior run's *authored* configuration — what a continuation builds on.

    Continuing reads this rather than the resolved form because resolved input
    paths are absolute: ``./datasets/coco8`` is stored as
    ``/home/you/datasets/coco8``, which does not exist on the cluster node you
    wanted to continue in. The authored form re-absolutizes wherever it runs.

    The cost, accepted: this holds only what was authored, so schema defaults are
    re-applied at continuation time and a default that moved since the parent ran
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
            raise RefusalError(f"cannot parse override {token!r}: {exc}") from None
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
            composed = compose(config_name=spec.config_name, overrides=full)
    except HydraException as exc:
        # A composition error is a message to the user about their configuration,
        # not a defect in Honbu, so it exits like one rather than as a traceback.
        raise RefusalError(f"composition failed: {exc}") from None
    container = OmegaConf.to_container(composed, resolve=True)
    if not isinstance(container, dict):
        raise RefusalError("composed configuration is not a mapping")
    container.pop("hydra", None)
    return container
