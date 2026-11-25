"""Library-agnostic terminal helpers for Honbu projects (D19).

Importing this module never pulls in Typer (enforced by ``test_layering``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TextIO

from honbu.core.targets import MANIFEST_NAME
from honbu.errors import HonbuError, RefusalError
from honbu.flow.compose import (
    ALL_CONFIG_SOURCES,
    ComposeSpec,
    ConfigSource,
    check_overrides,
    search_dirs,
)
from honbu.flow.project import ProjectSpec

#: Conventional CLI flag spellings shared across docs, presets, and default hints.
#: Projects are free to spell any flag differently and pass custom labels to
#: :func:`select_config_source` or ``cli_hint`` on target preparation.
CONVENTIONAL_FLAGS: Mapping[str, str] = MappingProxyType(
    {
        "config": "--config",
        "resolved": "--resolved",
        "fork_from": "--fork-from",
        "fork_artifact": "--fork-artifact",
        "config_dir": "--config-dir",
        "clobber": "--clobber",
        "resume": "--resume",
    }
)

_DEFAULT_SOURCE_FLAGS: Mapping[ConfigSource, str] = MappingProxyType(
    {
        "file": CONVENTIONAL_FLAGS["config"],
        "resolved": CONVENTIONAL_FLAGS["resolved"],
        "fork": CONVENTIONAL_FLAGS["fork_from"],
    }
)


@dataclass(frozen=True)
class DashRuleSplit:
    """Trailing argv tokens partitioned by the dash rule."""

    overrides: tuple[str, ...] = ()
    positionals: tuple[str, ...] = ()


def split_dash_rule(tokens: Sequence[str]) -> DashRuleSplit:
    """Partition trailing CLI tokens into Hydra overrides and bare positionals.

    Follows the dash rule:
    * Any token beginning with ``-`` is refused as an unknown option rather than
      silently forwarded to Hydra or treated as a positional.
    * Any dash-free token containing ``=`` or starting with ``~`` is a
      configuration override (covering ``key=val``, ``+key=val``, ``++key=val``,
      ``~key``, ``~key=val``, and ``group=option``).
    * Remaining tokens are bare positionals.
    """
    overrides: list[str] = []
    positionals: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            raise RefusalError(
                f"unknown option {token!r}; pass CLI options before overrides or check --help"
            )
        if "=" in token or token.startswith("~"):
            overrides.append(token)
        else:
            positionals.append(token)
    return DashRuleSplit(overrides=tuple(overrides), positionals=tuple(positionals))


def validate_override_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    """Validate trailing CLI tokens intended purely as Hydra overrides.

    Refuses dash-prefixed tokens (mistyped CLI options), bare non-override tokens,
    bare top-level comma multirun sweeps, and malformed override syntax (with
    brace-quoting advice for Honbu templates).
    """
    split = split_dash_rule(tokens)
    if split.positionals:
        unexpected = ", ".join(repr(p) for p in split.positionals)
        raise RefusalError(
            f"unexpected argument(s) {unexpected}; configuration overrides must use "
            "key=value or ~key syntax"
        )
    check_overrides(split.overrides)
    return split.overrides


def select_config_source(
    *,
    config: Path | str | None = None,
    resolved: Path | str | None = None,
    fork_from: Path | str | None = None,
    accepted: Sequence[ConfigSource] = ALL_CONFIG_SOURCES,
    flag_names: Mapping[ConfigSource, str] | None = None,
) -> tuple[ConfigSource | None, Path | str | None]:
    """Choose the single configuration source in play, independent of CLI flag names.

    Returns ``(source, reference)`` — or ``(None, None)`` when ``accepted`` is
    empty — enforcing mutual exclusivity and the command's accepted sources.
    Pass ``flag_names`` to customize how sources are named in refusal messages.
    """
    labels: dict[ConfigSource, str] = dict(_DEFAULT_SOURCE_FLAGS)
    if flag_names is not None:
        labels.update(flag_names)

    supplied: list[tuple[ConfigSource, Path | str]] = []
    if config is not None:
        supplied.append(("file", config))
    if resolved is not None:
        supplied.append(("resolved", resolved))
    if fork_from is not None:
        supplied.append(("fork", fork_from))

    if not accepted:
        if supplied:
            names = ", ".join(labels.get(src, src) for src, _ in supplied)
            raise RefusalError(
                f"this command takes no configuration; remove {names}"
            )
        return None, None

    if len(supplied) > 1:
        names = ", ".join(labels.get(src, src) for src, _ in supplied)
        raise RefusalError(f"configuration sources are mutually exclusive: {names}")

    if not supplied:
        if "default" not in accepted:
            offered = ", ".join(
                labels[src] for src in accepted if src in labels
            )
            raise RefusalError(
                "this command cannot compose a fresh configuration; "
                f"identify an existing one with {offered}"
            )
        return "default", None

    source, value = supplied[0]
    if source not in accepted:
        offered = ", ".join(
            labels[candidate] for candidate in accepted if candidate in labels
        ) or "default configuration"
        raise RefusalError(
            f"{labels.get(source, source)} is not accepted by this command; use {offered}"
        )
    return source, value


#: Suffix a positional config token may omit (Hydra composes ``.yaml`` only).
_YAML_SUFFIX = ".yaml"


def _candidates(base: Path, token: str) -> list[Path]:
    """The ``.yaml`` file ``token`` names relative to ``base`` (if present)."""
    direct = base / token
    candidate = (
        direct
        if direct.suffix.lower() == _YAML_SUFFIX
        else direct.with_name(direct.name + _YAML_SUFFIX)
    )
    return [candidate.resolve()] if candidate.is_file() else []


def _is_run_dir(path: Path) -> bool:
    from honbu.flow.run import CONFIG_DIRNAME, RESOLVED_NAME

    return path.is_dir() and (
        (path / MANIFEST_NAME).is_file()
        or (path / CONFIG_DIRNAME / RESOLVED_NAME).is_file()
    )


def resolve_positional_config(
    token: str | Path,
    *,
    compose_spec: ComposeSpec | None = None,
    project: ProjectSpec | None = None,
    extra_dirs: Sequence[Path | str] = (),
    cwd: Path | None = None,
    reject_run_dir: bool = True,
) -> Path:
    """Resolve a forgiving positional ``[CONFIG]`` token to one authored YAML file.

    Two channels are consulted and their matches deduplicated by resolved path:

    1. **Direct filesystem** — ``<tok>`` relative to ``cwd`` (or absolute),
       appending ``.yaml`` when omitted.
    2. **Search path** — the *first* (highest-precedence) directory of
       ``[*extra_dirs, local, packaged]`` holding ``<tok>`` (or ``<tok>.yaml``).
       Skipped for absolute, ``./``-anchored, or ``..``-traversing paths so an
       explicit filesystem path cannot escape a search directory or collide with
       it. Only the first hit counts, so a local copy shadowing a packaged
       default is one match rather than an ambiguity.

    Zero matches refuse (hinting that ``key=value`` overrides and ``--flags`` are
    spelled differently); more than one distinct match refuses listing every
    candidate and asking for the exact ``-c`` / ``--config``. A run directory
    refuses when ``reject_run_dir`` is set, pointing at ``--resolved`` or
    ``--fork-from``, because a positional config is something you author.
    """
    spec = (
        compose_spec
        if compose_spec is not None
        else (project.compose_spec if project is not None else None)
    )
    text = str(token)
    if not text.strip():
        raise RefusalError("an empty configuration name was given")
    token_path = Path(text)
    if token_path.suffix.lower() == ".yml":
        raise RefusalError(
            f"{text!r} uses a '.yml' extension; configuration files must use '.yaml'"
        )
    base = (cwd or Path.cwd()).resolve()
    direct_base = token_path if token_path.is_absolute() else base / token_path

    if _is_run_dir(direct_base):
        if reject_run_dir:
            raise RefusalError(
                f"{text!r} is a run directory, not an authored configuration; replay it "
                f"with {CONVENTIONAL_FLAGS['resolved']} or branch from it with "
                f"{CONVENTIONAL_FLAGS['fork_from']}"
            )

    found: list[Path] = list(_candidates(base, text))
    searched: tuple[Path, ...] = ()
    explicit_fs = (
        token_path.is_absolute()
        or ".." in token_path.parts
        or text.startswith("./")
        or text.startswith(".\\")
    )
    if not explicit_fs:
        searched = search_dirs(
            spec if spec is not None else ComposeSpec(packaged=None, local=None),
            extra_dirs,
            cwd=base,
        )
        for directory in searched:
            hits = _candidates(directory, text)
            if hits:
                found.extend(hits)
                break

    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    config_flag = CONVENTIONAL_FLAGS["config"]
    if not unique:
        where = [str(base)] + [str(path) for path in searched]
        listed = "\n  ".join(dict.fromkeys(where))
        raise RefusalError(
            f"no configuration named {text!r} was found in:\n  {listed}\n"
            "Configuration overrides are spelled key=value (or ~key), and options "
            "begin with --; check the token or pass an explicit "
            f"-c/{config_flag} path."
        )
    listed = "\n  ".join(str(path) for path in unique)
    raise RefusalError(
        f"configuration name {text!r} is ambiguous; it matches:\n  {listed}\n"
        f"Choose one explicitly with -c/{config_flag} <path>."
    )


@contextmanager
def exit_on_refusal(
    *,
    prog: str | None = None,
    exit_code: int = 1,
    interrupt_code: int = 130,
    on_error: Callable[[HonbuError], None] | None = None,
    stream: TextIO | None = None,
) -> Iterator[None]:
    """Translate :class:`~honbu.errors.HonbuError` into a clean non-zero exit.

    A refusal prints its message and exits without a traceback; any unexpected
    exception propagates with its traceback intact so defects stay diagnosable.
    Supply ``on_error`` to render structured output (for example under
    ``--format json``) instead of plain text on stderr.
    """
    target = stream if stream is not None else sys.stderr
    prefix = f"{prog}: " if prog else ""
    try:
        yield
    except HonbuError as refusal:
        if on_error is not None:
            on_error(refusal)
        else:
            print(f"{prefix}{refusal}", file=target)
        raise SystemExit(exit_code) from None
    except KeyboardInterrupt:
        print(f"{prefix}interrupted", file=target)
        raise SystemExit(interrupt_code) from None


def emit_warnings(
    warnings: Sequence[str],
    *,
    prefix: str = "warning",
    stream: TextIO | None = None,
) -> None:
    """Print collected framework warnings to stderr (or ``stream``)."""
    target = stream if stream is not None else sys.stderr
    for warning in warnings:
        print(f"{prefix}: {warning}", file=target)
