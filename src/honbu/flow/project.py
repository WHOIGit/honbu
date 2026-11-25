"""Passive project declaration bundle (D1, D19).

Eliminates repeating ``(root_type, compose_spec, hashes, templates,
config_hash_name)`` across dozens of call sites without introducing inversion of
control or CLI coupling. Every Honbu operation accepts ``project=PROJECT`` as an
optional convenience alongside direct keyword arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from honbu.core.blocks import CONFIG_HASH_NAME, validate_root_contract
from honbu.core.hashes import HashRegistry, validate_identity_fields
from honbu.core.templates import TemplateRegistry
from honbu.flow.compose import ComposeSpec


@dataclass(frozen=True)
class ProjectSpec:
    """Passive frozen bundle of a project's shared Honbu declarations.

    Runs :func:`~honbu.core.blocks.validate_root_contract` and
    :func:`~honbu.core.hashes.validate_identity_fields` on construction so schema
    and identity mistakes fail at import time. Holds no command callbacks, owns
    no CLI parser, and never invokes project code.
    """

    root_type: type[BaseModel]
    compose_spec: ComposeSpec
    hashes: HashRegistry = field(default_factory=lambda: HashRegistry([]))
    templates: TemplateRegistry | None = None
    config_hash_name: str = CONFIG_HASH_NAME
    #: Opt in to ``open_run`` writing ``config/cli.txt`` for CLI invocations.
    write_cli_txt: bool = False

    def __post_init__(self) -> None:
        validate_root_contract(self.root_type)
        validate_identity_fields(self.root_type, self.hashes)
