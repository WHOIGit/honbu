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

from honbu.errors import HonbuError, RefusalError
from honbu.flow.compose import ALL_CONFIG_SOURCES, ConfigSource, check_overrides

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
