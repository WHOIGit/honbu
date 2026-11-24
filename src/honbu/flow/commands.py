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

Commands here declare lifecycle and schema semantics only — never CLI flags,
options, or positionals (D19). Projects own their CLI surface completely and call
into :mod:`honbu.flow` with typed Python arguments.

Everything declared here is validated when the registry is constructed, i.e. at
import. A renamed configuration block should break the build, not a ``rmtree``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from honbu.core.hashes import HashRegistry, declared_path_exists
from honbu.errors import DeclarationError
from honbu.flow.compose import ALL_CONFIG_SOURCES, ConfigSource

CommandMode = Literal["produce", "augment"]


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
    """One command's lifecycle and schema metadata: write scope, hashes, and steps.

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
    #: Which declared steps constitute a complete run. None means all of them.
    #: A step left out is optional: not running it does not hold the run open. A
    #: step left *in* that neither runs nor is declared inapplicable leaves the run
    #: partial, which is what keeps a forgotten branch loud.
    completion: Sequence[str] | None = None
    #: Whether running this command is *work on the run*. False leaves the run's
    #: state exactly as found, recording only the invocation's own events — which
    #: covers both a command that prepares a run for later work (the run stays
    #: initialized) and an augmenting command, which can never advance a run.
    #:
    #: Deliberately a boolean rather than a target state: states derive from the
    #: timeline, and a command able to *name* one could declare a run complete
    #: having done nothing.
    advances_run: bool = True
    #: Which configuration sources this command accepts. An empty tuple means the
    #: command composes nothing, so a missing default root is not an error for it.
    config_sources: Sequence[ConfigSource] = ALL_CONFIG_SOURCES
    #: Optional default parent artifact key when forking via :func:`parent_record`.
    #: Callers may always select a different registered artifact key at invocation time.
    fork_artifact: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", (self.name,))
        if not self.name or not all(self.name):
            raise DeclarationError("a command name needs at least one non-empty segment")
        if self.mode == "augment":
            # Not a declaration a project gets to make: augmenting adds to a run
            # someone else produced, so it cannot speak to whether that run's work
            # happened.
            object.__setattr__(self, "advances_run", False)

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

    @property
    def required_steps(self) -> tuple[str, ...]:
        """The steps that must be satisfied for the run to be complete."""
        return self.step_names if self.completion is None else tuple(self.completion)

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

    Three checks, all at construction:

    1. no duplicate command names
    2. every declared dotted path is a real field of the root
    3. every declared hash name exists in the hash registry
    """

    def __init__(
        self,
        commands: Sequence[CommandSpec],
        *,
        root_type: type[BaseModel] | None = None,
        hashes: HashRegistry | None = None,
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

        unknown = sorted(set(command.completion or ()) - set(command.step_names))
        if unknown:
            raise DeclarationError(
                f"{command.invocation} requires steps it does not declare: {unknown}; "
                f"declared: {', '.join(command.step_names) or '(none)'}"
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


def field_default(root_type: type[BaseModel], dotted: str) -> object | None:
    """The schema default at ``dotted``, traversing direct submodels only."""
    from honbu.core.markers import parse_dotted, traversable_submodel

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
