"""The harness: argv to a finished run, and the exit code.

Two layers, deliberately separated. :func:`dispatch_command` threads one command
through the whole flow and knows about no CLI library. :class:`Application` is
Honbu's own dispatcher on top of it — longest-path matching, help generated from
the declarations, one exit code.

Why not Typer. Honbu already declares the command tree, every option with its
arity, and every positional with its required flag; it had to, because the argv
split runs before any CLI library sees ``argv`` and only declared arity makes a
following bare token unambiguous. Restating that in decorators is a second source
of truth for one surface — the drift pattern Honbu exists to remove. A project that
prefers its own parser calls :func:`dispatch_command` with the remaining tokens,
so nothing is lost but shell completion, which can be generated from the same
declarations later.

A refusal and a defect exit differently. ``RefusalError`` is a message to the user:
it prints and exits non-zero with no traceback. Anything else is a bug, so its
traceback survives — swallowing it would trade a diagnosable crash for a mystery.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from honbu.errors import DeclarationError, HonbuError, RefusalError
from honbu.flow.commands import CommandRegistry, CommandSpec, ParsedInvocation, parse_invocation
from honbu.flow.compose import (
    ComposeSpec,
    compose_config,
    compose_file,
    prior_config_path,
    refuse_active_sweep_block,
)
from honbu.flow.resolve import Resolution, resolve, strip_resolved_only
from honbu.core.hashes import HashRegistry, validate_identity_fields
from honbu.core.io import read_json
from honbu.core.identifiers import fresh_seed
from honbu.flow.run import COMPOSED_NAME, RESOLVED_NAME, Run, open_run
from honbu.core.targets import MANIFEST_NAME
from honbu.core.templates import TemplateContext, TemplateRegistry

#: A producing or augmenting command's work. Receives the live run; returns
#: nothing, because the best result is recorded live and *is* the comparison
#: substrate. Returning is completion, raising is failure, a signal is an
#: interruption.
WorkFn = Callable[[Run], None]

#: A command with a configuration but no target — inspection, reporting. Receives
#: the pure resolution, so no write is in reach.
InspectFn = Callable[[Resolution], None]

#: A command declaring no configuration source at all. Receives the parsed
#: invocation: its positionals and options are all there is.
BareFn = Callable[[ParsedInvocation], None]

HELP_FLAGS = frozenset({"-h", "--help"})


def _template_context(extras: Mapping[str, Any] | None = None) -> TemplateContext:
    """Clock and entropy, minted once per invocation.

    Made here rather than inside the renderer so rendering stays pure: one
    invocation cannot render the same token two ways, while two invocations of one
    composed configuration still get different fresh identities and so do not
    collide.
    """
    from datetime import datetime, timezone

    return TemplateContext(
        now_local=datetime.now(),
        now_utc=datetime.now(timezone.utc),
        seed=fresh_seed(),
        extras=dict(extras or {}),
    )


@dataclass(frozen=True)
class Handler:
    """One command plus the project function that does its work."""

    spec: CommandSpec
    work: WorkFn | InspectFn
    help: str = ""


def load_composed(
    parsed: ParsedInvocation,
    *,
    compose_spec: ComposeSpec,
    root_type: type[BaseModel],
    cwd: Path | None = None,
) -> tuple[Mapping[str, Any], Path | None, bool]:
    """Load whichever configuration source the invocation selected.

    Returns the composed mapping, the prior run directory when one was named, and
    whether this is a replay — which is what exempts a resolved configuration from
    the both-authored refusal on a directory and its template.
    """
    overrides = list(parsed.overrides)
    extra = parsed.options.get("--config-dir") or []

    match parsed.source:
        case None:
            return {}, None, False
        case "default":
            return (
                compose_config(compose_spec, overrides=overrides, extra_dirs=extra, cwd=cwd),
                None,
                False,
            )
        case "file":
            return compose_file(parsed.source_value or "", overrides=overrides), None, False
        case "resolved":
            # A replay keeps everything, so the recorded literal output directory
            # wins and the invocation lands in that same run.
            located, run_dir = prior_config_path(
                Path(parsed.source_value or ""), RESOLVED_NAME
            )
            return compose_file(located, overrides=overrides), run_dir, True
        case "continue":
            # A child is a new run, so it builds on the parent's *authored* form.
            # The resolved form would carry absolute input paths — correct on the
            # machine that produced it, wrong on the one you meant to continue in.
            located, run_dir = prior_config_path(
                Path(parsed.source_value or ""), COMPOSED_NAME
            )
            composed = compose_file(located, overrides=overrides)
            # Nothing is normally authored into a resolved-only field, so this is a
            # safety net rather than the mechanism: a project that does author one
            # must not have it inherited as though the child had resolved it.
            return strip_resolved_only(composed, root_type), run_dir, False

    raise RefusalError(f"unhandled configuration source {parsed.source!r}")


def _parent_record(spec: CommandSpec, run_dir: Path) -> dict[str, Any]:
    """Lineage for a continued run, read from the parent's manifest.

    Honbu records the link and nothing about its meaning: whether the child
    restores training state or only loads weights shows up in the child's own
    configuration.
    """
    manifest = read_json(run_dir / MANIFEST_NAME)
    if not isinstance(manifest, dict):
        raise RefusalError(f"{run_dir} has no readable {MANIFEST_NAME} to continue from")
    artifact = spec.continue_artifact
    stored = (manifest.get("artifacts") or {}).get(artifact)
    if stored is None:
        available = ", ".join(sorted(manifest.get("artifacts") or {})) or "(none)"
        raise RefusalError(
            f"{run_dir} registered no {artifact!r} artifact to continue from; "
            f"it has: {available}"
        )
    # The parent's configuration identity comes from its recorded digests, not its
    # identities: an identity is a declared name, and a digest is what gates
    # comparison.
    digests = manifest.get("hashes") or {}
    recorded = digests.get("config_hash") or {}
    return {
        "run_dir": str(run_dir),
        "config_hash": recorded.get("value") or "",
        "artifact": str(stored),
    }


def dispatch_command(
    handler: Handler,
    tokens: Sequence[str],
    *,
    root_type: type[BaseModel],
    hashes: HashRegistry,
    compose_spec: ComposeSpec,
    templates: TemplateRegistry | None = None,
    cwd: Path | None = None,
) -> None:
    """Thread one command from its remaining argv tokens to a finished run.

    Knows about no CLI library, so a project keeping its own parser calls this
    directly. The order here is the whole of L2: parse, compose, resolve, prepare,
    work, close out.
    """
    spec = handler.spec
    parsed = parse_invocation(spec, tokens)

    if not spec.config_sources:
        # Nothing to compose and nothing to resolve, so the handler gets what does
        # exist: the invocation. Three handler shapes, one per capability level —
        # a command reading only a checkpoint should not be broken by a missing
        # configuration root.
        handler.work(parsed)  # type: ignore[arg-type]
        return

    composed, prior_run, replay = load_composed(
        parsed, compose_spec=compose_spec, root_type=root_type, cwd=cwd
    )

    # Checked here rather than left to each project, because every command that
    # gets this far has exactly one target and the block describes many. A prepared
    # child never trips it: preparation consumes the block, so a child's
    # configuration does not carry one.
    refuse_active_sweep_block(composed, invocation=f"{spec.invocation}")

    if spec.target is None:
        resolution = resolve(
            composed,
            root_type=root_type,
            command=spec,
            hashes=hashes,
            context=_template_context(),
            templates=templates,
            cwd=cwd,
        )
        _emit(resolution.warnings)
        handler.work(resolution)  # type: ignore[arg-type]
        return

    extras: dict[str, Any] = {}
    if prior_run is not None:
        extras["source_run_dir"] = str(prior_run)

    resolution = resolve(
        composed,
        root_type=root_type,
        command=spec,
        hashes=hashes,
        context=_template_context(extras),
        templates=templates,
        cwd=cwd,
        existing_target=prior_run if spec.mode == "augment" else None,
        replay=replay,
    )
    _emit(resolution.warnings)

    parent = (
        _parent_record(spec, prior_run)
        if parsed.source == "continue" and prior_run is not None
        else None
    )
    common = {
        "composed": composed,
        "hashes": hashes,
        "argv_text": parsed.argv_text,
        "clobber": bool(parsed.options.get("--clobber")),
        "parent": parent,
    }

    with open_run(resolution, resume=bool(parsed.options.get("--resume")), **common) as run:  # type: ignore[arg-type]
        handler.work(run)  # type: ignore[arg-type]


def _emit(warnings: Sequence[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Honbu's own dispatcher
# --------------------------------------------------------------------------- #


@dataclass
class Application:
    """A project's command surface, derived entirely from its declarations."""

    name: str
    root_type: type[BaseModel]
    hashes: HashRegistry
    compose_spec: ComposeSpec
    templates: TemplateRegistry | None = None
    handlers: dict[str, Handler] = field(default_factory=dict)

    def command(self, spec: CommandSpec, *, help: str = "") -> Callable[[Any], Any]:
        """Register a command. The function receives a Run, or a Resolution when
        the command declares no target."""

        def register(fn: Any) -> Any:
            self.handlers[spec.slug] = Handler(spec=spec, work=fn, help=help)
            return fn

        return register

    def validate(self) -> None:
        """Every declaration checked at once, at import rather than at rmtree."""
        specs = [handler.spec for handler in self.handlers.values()]
        CommandRegistry(
            specs,
            root_type=self.root_type,
            hashes=self.hashes,
            deferred_paths=self._deferred_paths(),
        )
        validate_identity_fields(self.root_type, self.hashes)
        for spec in specs:
            if "continue" in spec.config_sources and spec.continue_artifact is None:
                raise DeclarationError(
                    f"{spec.invocation} accepts --continue-from but names no artifact "
                    "to continue from; set continue_artifact"
                )

    def _deferred_paths(self) -> tuple[str, ...]:
        """Config paths fed by a deferred hash — what a target template may not name."""
        from honbu.core.markers import identity_bindings

        deferred = set(self.hashes.deferred_names)
        return tuple(
            ".".join(path)
            for path, hash_name in identity_bindings(self.root_type)
            if hash_name in deferred
        )

    # ---- dispatch ---------------------------------------------------------

    def match(self, tokens: Sequence[str]) -> tuple[Handler, tuple[str, ...]] | None:
        """Longest registered command path first, so `inspect config-compare` beats
        any shorter prefix that happens to be registered too."""
        for length in range(min(len(tokens), 4), 0, -1):
            slug = "-".join(tokens[:length])
            handler = self.handlers.get(slug)
            if handler is not None:
                return handler, tuple(tokens[length:])
        return None

    def dispatch(self, argv: Sequence[str], *, cwd: Path | None = None) -> int:
        self.validate()
        tokens = list(argv)
        if not tokens or (len(tokens) == 1 and tokens[0] in HELP_FLAGS):
            print(self.usage())
            return 0

        matched = self.match(tokens)
        if matched is None:
            print(
                f"{self.name}: unknown command {' '.join(tokens)!r}\n\n{self.usage()}",
                file=sys.stderr,
            )
            return 2

        handler, remainder = matched
        if any(token in HELP_FLAGS for token in remainder):
            print(self.command_help(handler))
            return 0

        try:
            dispatch_command(
                handler,
                remainder,
                root_type=self.root_type,
                hashes=self.hashes,
                compose_spec=self.compose_spec,
                templates=self.templates,
                cwd=cwd,
            )
        except HonbuError as refusal:
            # A message to the user, not a defect: no traceback. Anything that is
            # not a HonbuError propagates with its traceback intact.
            print(f"{self.name}: {refusal}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print(f"{self.name}: interrupted", file=sys.stderr)
            return 130
        return 0

    def main(self, argv: Sequence[str] | None = None) -> None:
        raise SystemExit(self.dispatch(sys.argv[1:] if argv is None else argv))

    # ---- help -------------------------------------------------------------

    def usage(self) -> str:
        lines = [f"{self.name} <command> [options] [key=value ...]", "", "commands:"]
        width = max((len(h.spec.invocation) for h in self.handlers.values()), default=0)
        for slug in sorted(self.handlers):
            handler = self.handlers[slug]
            lines.append(f"  {handler.spec.invocation:<{width}}  {handler.help}".rstrip())
        return "\n".join(lines)

    def command_help(self, handler: Handler) -> str:
        from honbu.flow.compose import FRAMEWORK_OPTIONS, SOURCE_OPTIONS

        spec = handler.spec
        positionals = " ".join(
            p.name if p.required else f"[{p.name}]" for p in spec.positionals
        )
        lines = [
            f"{self.name} {spec.invocation} {positionals} [options] [key=value ...]".replace(
                "  ", " "
            )
        ]
        if handler.help:
            lines += ["", handler.help]

        if spec.config_sources:
            sources = [
                SOURCE_OPTIONS[source]
                for source in spec.config_sources
                if source in SOURCE_OPTIONS
            ]
            default = "the default root" if "default" in spec.config_sources else None
            offered = ", ".join(filter(None, [default, *sources]))
            lines += ["", f"configuration from: {offered}"]
        else:
            lines += ["", "takes no configuration"]

        # From the same derivation the parser uses, so help cannot advertise an
        # option the parser refuses, or hide one it accepts.
        applicable = set(spec.applicable_options())
        relevant = [o for o in FRAMEWORK_OPTIONS if o.name in applicable]
        lines += ["", "options:"]
        for option in [*relevant, *spec.options]:
            suffix = " VALUE" if option.takes_value else ""
            lines.append(f"  {option.name}{suffix}")

        if spec.steps:
            lines += ["", "steps: " + ", ".join(spec.step_names)]
        if spec.hashes:
            lines += ["identities: " + ", ".join(spec.hashes)]
        return "\n".join(lines)
