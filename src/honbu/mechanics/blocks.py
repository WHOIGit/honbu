"""The base configuration blocks a project assembles its root from.

Exactly four things are requirable, because they are the only top-level
structures the sibling projects genuinely share: an experiment block, a runtime
block, an output root, and at least one output block. Blocks that merely share a
*name* between projects — ``results`` holds unrelated fields in each — are not
contracts, and treating the coincidence as one would force one project's
semantics onto the other for nothing.

The contract guard checks presence and base type, and stops there. An earlier
draft policed what subclasses did to inherited fields; that forbade something one
project already relies on (five output blocks subclass a base purely to change
``dir_template``'s default, which requires restating the whole field) while
guarding a failure that announces itself the same day. Projects get the fields
they need, with documentation rather than guardrails.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel

from honbu.errors import DeclarationError
from honbu.mechanics.markers import (
    IDENTITY,
    PortablePath,
    ResolvedString,
    TemplatedIdentity,
)
from honbu.mechanics.model import StrictModel

#: The conventional name for a whole-configuration digest — a convention, not a
#: reservation. Honbu neither requires the declaration nor binds it; the name exists
#: so the adoption guide and the projects spell it the same way.
CONFIG_HASH_NAME = "config_hash"


class ExperimentConfig(StrictModel):
    """Names the family of runs a configuration belongs to."""

    name: str


class RuntimeConfig(StrictModel):
    """Run identities and the invocation seed.

    ``seed`` is authored, and stays null when it is not. Honbu deliberately does not
    fill one: assigning after the digest is computed would let the digest say null
    while the run used a number, and assigning before would give every unseeded
    invocation its own configuration identity. A null seed honestly means "no seed
    pinned"; pinning it is the project's call.

    ``run_id`` and ``config_id`` hold templates rendered in place, so the run
    directory can reference them. ``run_id`` defaults to a fresh identity per
    invocation, so two runs of one configuration do not collide even when the output
    template carries no timestamp.

    ``config_id`` defaults to **null**, and ``config_hash`` carries **no** binding to
    any hash. Honbu reserves no hash name and binds nothing of its own accord: a
    project that declares no hashes at all is a valid project, and no shipped default
    may reference a field the framework does not itself guarantee. An earlier version
    defaulted ``config_id`` to ``"{runtime.config_hash:coolname}"`` and bound
    ``config_hash`` to a reserved declaration to make that default work — which made
    declaring a hash mandatory and contradicted the rule that identities are declared
    rather than inferred.

    So a hash-derived configuration identity is opt-in, and it is two lines::

        class Runtime(RuntimeConfig):
            config_hash: Annotated[ResolvedString, FromHash(CONFIG_HASH_NAME)] = None
            config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"

    Nearly every project wants them. Without a configuration-derived component, an
    output template reaches for ``run_id`` or ``{:timestamp}``, both fresh per
    invocation — and a run directory named from fresh entropy cannot be re-found by a
    later ``--resume``. That is boilerplate the adoption guide leads with rather than
    something enforced here. ``config_id`` may equally bind to a per-command digest,
    which is often the more useful identity.

    ``sweep_id`` is assigned, never authored — it holds no value in a composed
    configuration — so it is derived and resolved-only like the other assigned
    fields, and therefore excluded from every digest. That exclusion is load-bearing:
    without it a sweep child's digest would incorporate its sweep membership, and the
    child could not be compared against the same configuration run by hand. Being
    resolved-only also means continuing from a sweep child does not inherit the
    parent's sweep membership, which is correct — the continuation is a new run, not
    a thirteenth member.

    The identity-marked fields appear in the run manifest; the digests do not, because
    not every hash warrants an identity.

    There is deliberately no ``command`` field. A configuration is not per-command:
    putting the active command in it makes the resolved configuration differ between
    ``train`` and ``eval`` over one authored file, which forks the configuration
    index and defeats the whole point of two invocations sharing a bundle. The
    command is an invocation fact, recorded per invocation in the manifest, and
    available to templates as ``{:command}`` in the invocation namespace alongside
    the clock and the fresh identity.
    """

    seed: int | None = None
    run_id: TemplatedIdentity = "{:coolname}"
    config_id: TemplatedIdentity | None = None
    config_hash: ResolvedString = None
    sweep_id: Annotated[ResolvedString, IDENTITY] = None


class OutputBlock(StrictModel):
    """A family of outputs with one directory.

    Either ``dir`` or ``dir_template`` is authored, never both — a rule enforced
    at resolution rather than here, because after resolution both are legitimately
    present: the template stays as the authored record while ``dir`` holds what it
    rendered to. Neither field enters a digest; they decide where artifacts land,
    not what is computed.
    """

    dir: PortablePath | None = None
    dir_template: str | None = None


class OutputLeaf(StrictModel):
    """One directory inside an output block, relative to it.

    Leaves template too. An override that changes a named hash can flow into
    ``dir_template`` (``"results/{inference_hash:coolname}"``), so re-running an
    augmenting command with different parameters writes a new leaf instead of
    colliding with the old one, and both results survive in one run directory.
    """

    enabled: bool = True
    dir: PortablePath | None = None
    dir_template: str | None = None


#: Field name → required base type. ``output_root`` is checked by annotation.
REQUIRED_BLOCKS: dict[str, type[BaseModel]] = {
    "experiment": ExperimentConfig,
    "runtime": RuntimeConfig,
}


def _annotation_types(annotation: Any) -> tuple[type, ...]:
    """Concrete classes reachable from an annotation, unions and Annotated included."""
    from typing import Union, get_args, get_origin

    if isinstance(annotation, type):
        return (annotation,)
    origin = get_origin(annotation)
    if origin is None:
        return ()
    if origin is Union or str(origin) == "types.UnionType":
        return tuple(t for arg in get_args(annotation) for t in _annotation_types(arg))
    # Annotated[X, ...] and other single-parameter wrappers
    args = get_args(annotation)
    return _annotation_types(args[0]) if args else ()


def output_block_fields(root_type: type[BaseModel]) -> tuple[str, ...]:
    """Top-level fields whose type is an :class:`OutputBlock` or a subclass."""
    found = []
    for name, info in root_type.model_fields.items():
        if any(issubclass(t, OutputBlock) for t in _annotation_types(info.annotation)):
            found.append(name)
    return tuple(found)


def validate_root_contract(root_type: type[BaseModel]) -> None:
    """Verify a project root carries the four requirable blocks.

    Presence and base type only. What a project does to inherited fields — adding,
    retyping, re-defaulting, re-constraining, dropping a marker — is its own call.
    """
    fields = root_type.model_fields
    for name, base in REQUIRED_BLOCKS.items():
        info = fields.get(name)
        if info is None:
            raise DeclarationError(
                f"{root_type.__name__} is missing the required {name!r} block "
                f"(expected {base.__name__} or a subclass)"
            )
        types = _annotation_types(info.annotation)
        if not any(isinstance(t, type) and issubclass(t, base) for t in types):
            raise DeclarationError(
                f"{root_type.__name__}.{name} must be {base.__name__} or a subclass, "
                f"not {info.annotation!r}"
            )

    if "output_root" not in fields:
        raise DeclarationError(
            f"{root_type.__name__} is missing 'output_root', the containment root "
            "for destructive operations"
        )

    if not output_block_fields(root_type):
        raise DeclarationError(
            f"{root_type.__name__} declares no output block; at least one field must "
            f"be an {OutputBlock.__name__} or a subclass"
        )
