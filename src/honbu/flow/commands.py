"""Command declarations: what a command writes, what it hashes, and its steps.

The scope of a destructive operation comes from here, not from a flag. Every
other design puts ``--clobber`` at one fixed radius — the run directory — which is
wrong for the common case of re-rendering figures inside a finished run, and
dangerously coarse for anything else. A declaration names the radius; the flag
supplies only permission.

Destructive authority is also deliberately *not* conferred by a type. An earlier
draft made a base ``OutputBlock`` mean "clobberable", so subclassing or copying a
block would silently grant permission to delete a directory. That is the mistake
:ref:`D5` rejected for hashes — do not freeze policy into the artifact, ask the
operation — and the shape of the projects confirms it: one root configuration has
a dataset-product directory and a run directory, cleared by different commands.

Everything declared here is validated when the registry is constructed, i.e. at
import. A renamed configuration block should break the build, not a ``rmtree``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from honbu.errors import DeclarationError, RefusalError
from honbu.flow.compose import (
    ALL_CONFIG_SOURCES,
    ConfigSource,
    OptionSpec,
    selected_source,
    split_argv,
)
from honbu.mechanics.hashes import HashRegistry, declared_path_exists
from honbu.mechanics.templates import template_value_paths

CommandMode = Literal["produce", "augment"]


@dataclass(frozen=True)
class Positional:
    """A bare argv token the command takes.

    Positionals reach the command, never the configuration. Letting one assign a
    field would give two spellings for the same value, which is how the dash-rule
    grammar starts leaking.
    """

    name: str
    required: bool = True


@dataclass(frozen=True)
class TargetSpec:
    """Where a producing command writes, as three dotted configuration paths.

    Three rather than one because a directory is authored as a *pattern*
    (``training_outputs.dir_template``) and resolved into a *different* field
    (``training_outputs.dir``) — and both are real: authoring ``dir`` directly is
    how you pin an exact directory and skip templating. ``root`` names the
    containment root, which is per-command precisely because a dataset cache
    legitimately lives outside the run root.
    """

    template: str
    resolved: str
    root: str

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.template, self.resolved, self.root)


#: What resuming does to a step's own prior output. ``continue`` treats it as
#: resume state; ``restart`` treats it as spoiled work and clears the scope.
ResumePolicy = Literal["continue", "restart"]


@dataclass(frozen=True)
class StepSpec:
    """One named phase of a command's work, with its own write scope.

    Steps are declared rather than free-form strings for two reasons. Resume skips
    completed steps by name, so a typo would never match and would silently re-run
    the step it was meant to skip; and clearing a scope needs the scope, which has
    to be written down somewhere regardless.

    ``on_resume`` distinguishes two opposite meanings a step's output can have, and
    only the project knows which applies. A dataset-preparation step that wrote
    half a manifest holds spoiled work: ``restart``. A training step's checkpoints
    *are* the resume state, so clearing them turns "continue from epoch 10" into
    "start over": ``continue``.

    Continuing is the default because the asymmetry is severe. Wrongly restarting
    destroys hours of irrecoverable work silently; wrongly continuing produces a
    visibly wrong result the step usually trips over itself. The destructive option
    is the one you type.

    Honbu ships no step names. The vocabulary is entirely the project's.
    """

    name: str
    scope: Sequence[str] = ()
    on_resume: ResumePolicy = "continue"


@dataclass(frozen=True)
class CommandSpec:
    """One command: its place in the command tree, its write scope, its hashes.

    ``name`` is a tuple because a flat string is lossy. One project has
    ``inspect config-compare``; flattened to ``inspect-config-compare`` you cannot
    recover whether the leaf or the group carried the hyphen. Each consumer joins
    it the way it needs rather than Honbu picking one lossy form for all of them.
    """

    name: tuple[str, ...]
    mode: CommandMode = "produce"
    target: TargetSpec | None = None
    #: Dotted paths an augmenting command may clear. Unused when ``mode`` is produce,
    #: where the target directory is the scope.
    scope: Sequence[str] = ()
    hashes: Sequence[str] = ()
    steps: Sequence[StepSpec] = field(default_factory=tuple)
    #: Which configuration sources this command accepts. An empty tuple means the
    #: command composes nothing, so a missing default root is not an error for it.
    config_sources: Sequence[ConfigSource] = ALL_CONFIG_SOURCES
    positionals: Sequence[Positional] = ()
    #: The artifact key a continued run picks up from. Required when the command
    #: accepts the continue source: Honbu records the link, and only the project
    #: knows which of its artifacts is the one to continue from.
    continue_artifact: str | None = None
    #: Project options, so the argv split knows their arity before the CLI
    #: framework parses anything.
    options: Sequence[OptionSpec] = ()

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", (self.name,))
        if not self.name or not all(self.name):
            raise DeclarationError("a command name needs at least one non-empty segment")

    @property
    def slug(self) -> str:
        """Flattened, for filenames and dictionary keys."""
        return "-".join(self.name)

    @property
    def invocation(self) -> str:
        """As typed, for messages and reproduce lines."""
        return " ".join(self.name)

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def step(self, name: str) -> StepSpec:
        for spec in self.steps:
            if spec.name == name:
                return spec
        raise DeclarationError(
            f"{self.invocation} declares no step {name!r}; declared: "
            f"{', '.join(self.step_names) or '(none)'}"
        )


class CommandRegistry:
    """A project's commands, validated against its schema and hash registry.

    Four checks, all at construction:

    1. no duplicate command names
    2. every declared dotted path is a real field of the root
    3. every declared hash name exists in the hash registry
    4. no producing command's target template names a value a deferred hash
       produces — you cannot write into a directory whose name you learn by
       writing
    """

    def __init__(
        self,
        commands: Sequence[CommandSpec],
        *,
        root_type: type[BaseModel] | None = None,
        hashes: HashRegistry | None = None,
        deferred_paths: Sequence[str] = (),
    ) -> None:
        slugs = [command.slug for command in commands]
        duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
        if duplicates:
            raise DeclarationError(f"duplicate command declarations: {sorted(duplicates)}")

        self._commands = {command.slug: command for command in commands}

        for command in commands:
            self._validate_shape(command)
            if root_type is not None:
                self._validate_paths(command, root_type)
            if hashes is not None:
                self._validate_hashes(command, hashes)
            self._validate_deferred(command, tuple(deferred_paths), root_type)

    @staticmethod
    def _validate_shape(command: CommandSpec) -> None:
        if command.mode == "produce" and command.target is None and command.scope:
            raise DeclarationError(
                f"{command.invocation} declares a scope but no target; a producing "
                "command's scope is its target directory"
            )
        if command.mode == "augment" and command.target is None:
            raise DeclarationError(
                f"{command.invocation} augments but declares no target to locate the "
                "run directory it augments"
            )
        names = list(command.step_names)
        repeated = {name for name in names if names.count(name) > 1}
        if repeated:
            raise DeclarationError(
                f"{command.invocation} declares duplicate steps: {sorted(repeated)}"
            )

        unknown_sources = sorted(set(command.config_sources) - set(ALL_CONFIG_SOURCES))
        if unknown_sources:
            raise DeclarationError(
                f"{command.invocation} declares unknown configuration sources: "
                f"{unknown_sources}"
            )
        if command.mode == "augment" and "default" in command.config_sources:
            raise DeclarationError(
                f"{command.invocation} augments an existing run, so it cannot accept "
                "a freshly composed root; drop 'default' from config_sources"
            )
        if command.target is not None and not command.config_sources:
            raise DeclarationError(
                f"{command.invocation} declares a target but no configuration source "
                "to resolve it from"
            )

        optional_seen = False
        for positional in command.positionals:
            if positional.required and optional_seen:
                raise DeclarationError(
                    f"{command.invocation}: required positional "
                    f"{positional.name!r} follows an optional one"
                )
            optional_seen = optional_seen or not positional.required

    @staticmethod
    def _validate_paths(command: CommandSpec, root_type: type[BaseModel]) -> None:
        declared: list[str] = list(command.scope)
        if command.target is not None:
            declared.extend(command.target.paths)
        for step in command.steps:
            declared.extend(step.scope)
        for dotted in declared:
            if not declared_path_exists(root_type, dotted):
                raise DeclarationError(
                    f"{command.invocation} declares path {dotted!r}, which is not a "
                    f"field of {root_type.__name__}"
                )

    @staticmethod
    def _validate_hashes(command: CommandSpec, hashes: HashRegistry) -> None:
        unknown = sorted(set(command.hashes) - set(hashes.names))
        if unknown:
            raise DeclarationError(
                f"{command.invocation} declares undeclared hashes: {unknown}"
            )

    @staticmethod
    def _validate_deferred(
        command: CommandSpec,
        deferred_paths: tuple[str, ...],
        root_type: type[BaseModel] | None,
    ) -> None:
        if command.target is None or command.mode != "produce":
            return
        if not deferred_paths or root_type is None:
            return
        template = field_default(root_type, command.target.template)
        if not isinstance(template, str):
            return
        for path in template_value_paths(template):
            if path in deferred_paths:
                raise DeclarationError(
                    f"{command.invocation} target template references {path!r}, which "
                    "is deferred to work. The output directory must be knowable "
                    "before work begins."
                )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._commands))

    def get(self, name: str | Sequence[str]) -> CommandSpec:
        slug = name if isinstance(name, str) else "-".join(name)
        try:
            return self._commands[slug]
        except KeyError:
            raise DeclarationError(
                f"unknown command {slug!r}; declared: {', '.join(self.names)}"
            ) from None


@dataclass(frozen=True)
class ParsedInvocation:
    """One command invocation, routed. The whole of argv, understood."""

    command: CommandSpec
    overrides: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    positionals: Mapping[str, str] = field(default_factory=dict)
    source: ConfigSource | None = None
    source_value: str | None = None

    @property
    def argv_text(self) -> str:
        """What the manifest records, so a run says how it was produced."""
        parts = [self.command.invocation, *self.positionals.values()]
        for name, value in self.options.items():
            parts.extend([name] if value is True else [name, *_as_list(value)])
        parts.extend(self.overrides)
        return " ".join(parts)


def _as_list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def parse_invocation(command: CommandSpec, tokens: Sequence[str]) -> ParsedInvocation:
    """Split argv for one command and match it against the command's declaration."""
    split = split_argv(tokens, options=command.options)
    source, value = selected_source(split.options, accepted=command.config_sources)

    declared = list(command.positionals)
    required = [p for p in declared if p.required]
    if len(split.positionals) > len(declared) or len(split.positionals) < len(required):
        expected = " ".join(
            p.name if p.required else f"[{p.name}]" for p in declared
        ) or "(none)"
        raise RefusalError(
            f"{command.invocation} takes positionals: {expected}; "
            f"got {len(split.positionals)}"
        )

    return ParsedInvocation(
        command=command,
        overrides=split.overrides,
        options=split.options,
        positionals=dict(zip((p.name for p in declared), split.positionals)),
        source=source,
        source_value=value,
    )


def field_default(root_type: type[BaseModel], dotted: str) -> object | None:
    """The schema default at ``dotted``, traversing direct submodels only.

    Used to read a target's declared ``dir_template`` at import. It catches the
    template a project committed, which is the one that matters — a configuration
    may still override it, so the resolver repeats the check at render time
    against the actual value.
    """
    from honbu.mechanics.markers import parse_dotted, traversable_submodel

    segments = parse_dotted(dotted)
    current: type[BaseModel] | None = root_type
    for index, segment in enumerate(segments):
        if current is None:
            return None
        info = getattr(current, "model_fields", {}).get(segment)
        if info is None:
            return None
        if index == len(segments) - 1:
            return info.default
        current = traversable_submodel(info.annotation)
    return None
