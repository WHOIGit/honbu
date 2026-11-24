"""Output-path template rendering. Pure: the same inputs always give the same string.

Grammar, four forms:

    {experiment.name}                 a resolved-configuration value
    {experiment.name:slug}            value, through a registered function
    {training.epochs:03d}             value, through a format specification
    {:timestamp} {:coolname} {:command}   the invocation namespace

An empty head is the **invocation namespace** — everything Honbu supplies rather
than reads from the configuration. Keeping it in its own namespace means no
reserved top-level names, so a project may name a block ``timestamp`` without
shadowing anything, and a reader can see at a glance that ``{:timestamp}`` is not
a field to go looking for in the YAML.

Purity is about the renderer, not about the run. Clock and entropy are produced
once per invocation by the resolver and handed in through
:class:`TemplateContext`, so two invocations of one composed configuration still
get different fresh identities — while a single invocation cannot render the same
token two different ways, which is what would happen if the renderer generated
entropy itself.

Nothing falls back. An unknown value, an unregistered function, or a non-scalar
raises. The predecessor of this module hashed the *token text* when a path lookup
missed, so a typo produced a stable, plausible slug — the same name for every run,
forever, with the collision hidden behind a timestamp in the same template.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from honbu.errors import TemplateError
from honbu.core.identifiers import deterministic_id
from honbu.core.paths import sanitization_warning, sanitize_relative_path

_TOKEN = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class TemplateContext:
    """Everything a template function may read. Fixed for one resolution."""

    now_local: datetime
    now_utc: datetime
    seed: str
    extras: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def now(cls, *, extras: Mapping[str, Any] | None = None) -> TemplateContext:
        """Mint clock and fresh per-invocation entropy once for a resolution."""
        from datetime import timezone

        from honbu.core.identifiers import fresh_seed

        return cls(
            now_local=datetime.now(),
            now_utc=datetime.now(timezone.utc),
            seed=fresh_seed(),
            extras=dict(extras or {}),
        )


#: ``value`` is None for the invocation form ``{:name}``, and the resolved
#: configuration value for ``{path:name}``. Each function decides which it accepts.
TemplateFunction = Callable[[TemplateContext, Any | None], str]


@dataclass(frozen=True)
class TemplateFunctionSpec:
    name: str
    function: TemplateFunction
    #: True when the result is a whole path rather than one component, so the
    #: renderer must not sanitize its separators away.
    emits_path: bool = False


def _no_value(name: str, value: Any | None) -> None:
    if value is not None:
        raise TemplateError(f"{{:{name}}} takes no value")


def _require_value(name: str, value: Any | None) -> Any:
    if value is None:
        raise TemplateError(f"{name} requires a value: use {{some.path:{name}}}")
    return value


def _timestamp(context: TemplateContext, value: Any | None) -> str:
    _no_value("timestamp", value)
    return context.now_local.strftime("%Y-%m-%d_%H-%M-%S")


def _timestamp_utc(context: TemplateContext, value: Any | None) -> str:
    _no_value("timestamp_utc", value)
    return context.now_utc.strftime("%Y-%m-%d_%H-%M-%SZ")


def _coolname(context: TemplateContext, value: Any | None) -> str:
    if value is None:
        # {:coolname} — fresh per invocation, so reruns of one composed config do
        # not collide even when the template carries no timestamp.
        return deterministic_id("seed", context.seed)
    # {runtime.config_hash:coolname} — stable, so reruns of one config land together.
    return deterministic_id("value", str(value))


def _slug(context: TemplateContext, value: Any | None) -> str:
    text = str(_require_value("slug", value)).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-") or "_"


def _command(context: TemplateContext, value: Any | None) -> str:
    _no_value("command", value)
    try:
        return str(context.extras["command"])
    except KeyError:
        raise TemplateError("{:command} is unavailable outside a command") from None


def _source_run_dir(context: TemplateContext, value: Any | None) -> str:
    _no_value("source_run_dir", value)
    try:
        return str(context.extras["source_run_dir"])
    except KeyError:
        raise TemplateError("{:source_run_dir} is unavailable for this command") from None


BUILTIN_FUNCTIONS: tuple[TemplateFunctionSpec, ...] = (
    TemplateFunctionSpec("timestamp", _timestamp),
    TemplateFunctionSpec("timestamp_utc", _timestamp_utc),
    TemplateFunctionSpec("coolname", _coolname),
    TemplateFunctionSpec("slug", _slug),
    TemplateFunctionSpec("command", _command),
    TemplateFunctionSpec("source_run_dir", _source_run_dir, emits_path=True),
)


class TemplateRegistry:
    """The token functions available to templates.

    Whether a project may override a framework-provided function is the project's
    decision, made once when it constructs the registry, rather than per call.
    """

    def __init__(
        self,
        specs: Iterable[TemplateFunctionSpec] = BUILTIN_FUNCTIONS,
        *,
        allow_override: bool = False,
    ) -> None:
        self._allow_override = allow_override
        self._specs: dict[str, TemplateFunctionSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: TemplateFunctionSpec) -> None:
        if not spec.name.isidentifier():
            # Names must be identifiers so a function can never shadow a format
            # specification: the renderer checks the registry before format().
            raise ValueError(f"template function name must be an identifier: {spec.name!r}")
        if spec.name in self._specs and not self._allow_override:
            raise ValueError(
                f"template function {spec.name!r} is already registered and this "
                "registry does not allow overrides"
            )
        self._specs[spec.name] = spec

    def get(self, name: str) -> TemplateFunctionSpec | None:
        return self._specs.get(name)


def template_value_paths(template: str) -> tuple[str, ...]:
    """The dotted configuration paths a template reads, ignoring the invocation
    namespace and the function or format suffix.

    Lives beside the renderer so the grammar has one definition: a caller that
    re-derived it — the command registry checking that a target template does not
    name a deferred value — would drift the moment the grammar gained a form.
    """
    paths = []
    for match in _TOKEN.finditer(template):
        head = match.group(1).partition(":")[0]
        if head:
            paths.append(head)
    return tuple(paths)


def _lookup(values: Mapping[str, Any], dotted: str) -> Any:
    current: Any = values
    for part in dotted.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(dotted)
            current = current[part]
        elif isinstance(current, (list, tuple)):
            if not part.lstrip("-").isdigit():
                raise KeyError(dotted)
            index = int(part)
            if not -len(current) <= index < len(current):
                raise KeyError(dotted)
            current = current[index]
        else:
            raise KeyError(dotted)
    return current


@dataclass(frozen=True)
class RenderedTemplate:
    text: str
    warnings: tuple[str, ...] = ()


def render_template(
    template: str,
    values: Mapping[str, Any],
    *,
    context: TemplateContext,
    registry: TemplateRegistry,
    path_fields: frozenset[str] = frozenset(),
) -> RenderedTemplate:
    """Render ``template``.

    ``path_fields`` names the dotted configuration paths carrying the portable-path
    marker. Their values are whole paths, so they skip component sanitization —
    otherwise an absolute path substituted into a template would lose its leading
    separator and come back relative.
    """
    warnings: list[str] = []

    def emit(text: str, *, sanitize: bool) -> str:
        if not sanitize:
            return text
        cleaned = sanitize_relative_path(text)
        warning = sanitization_warning(text, cleaned)
        if warning is not None:
            warnings.append(warning)
        return cleaned

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        head, separator, tail = token.partition(":")

        if not head:
            if not separator or not tail:
                raise TemplateError(f"template {template!r} has an empty token")
            spec = registry.get(tail)
            if spec is None:
                raise TemplateError(f"template {template!r}: unknown function {tail!r}")
            return emit(spec.function(context, None), sanitize=not spec.emits_path)

        try:
            value = _lookup(values, head)
        except KeyError:
            raise TemplateError(
                f"template {template!r} references unknown value {head!r}"
            ) from None

        if value is None:
            # A present-but-null field is not the invocation namespace. Without this,
            # a function with both forms — {:coolname} fresh, {path:coolname} stable
            # — receives None and cannot tell "no value was named" from "the value
            # named is null", so it silently mints entropy and the run gets a
            # plausible random identity. That is the same failure the no-fallback
            # rule above exists to prevent, arriving one level down.
            raise TemplateError(
                f"template {template!r}: {head!r} is null. It is either unset in the "
                "configuration or fed by a hash this command does not compute."
            )

        spec = registry.get(tail) if separator else None
        if spec is not None:
            return emit(spec.function(context, value), sanitize=not spec.emits_path)

        if not isinstance(value, (str, int, float, bool)):
            raise TemplateError(
                f"template {template!r}: {head!r} is {type(value).__name__}, not a scalar"
            )
        if separator:
            try:
                text = format(value, tail)
            except (ValueError, TypeError) as exc:
                raise TemplateError(
                    f"template {template!r}: {tail!r} is neither a registered template "
                    f"function nor a valid format specifier for {type(value).__name__}: {exc}"
                ) from None
        else:
            text = str(value)
        return emit(text, sanitize=head not in path_fields)

    rendered = _TOKEN.sub(replace, template)
    if "{" in rendered or "}" in rendered:
        raise TemplateError(f"unresolved template: {template!r} -> {rendered!r}")
    return RenderedTemplate(text=rendered, warnings=tuple(warnings))
