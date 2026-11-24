"""Honbu — the headquarters that defines the forms.

A framework for run-once, config-driven, content-addressed supervised training
runs of the shape ``config x fixed-dataset -> artifacts + metrics``. The
consuming project holds the main loop and reaches into Honbu; Honbu never inverts
control, so a project adopts the depth it wants and hand-rolls the rest.

One install carries every area; a project opts out of an area by not importing
it (D20). Layer boundaries are enforced by test:

* **``honbu.core``** — the canonical encoder, hash declarations and artifacts,
  the strict model base, base configuration blocks, field markers and walkers,
  atomic writes, path portability, template rendering, and collision-safe targets.
* **``honbu.flow``** — pure-Python composition, typed source loading, lifecycle-only
  command declarations, the fixed resolution phase order, the provenance bundle,
  the live run manifest, and passive :class:`~honbu.flow.ProjectSpec`.
* **Peer areas** — ``honbu.cli`` (opt-in CLI helpers and ``honbu.cli.presets`` for
  Typer), ``honbu.sweeps`` (config-defined sweeps), ``honbu.compare`` (N-way config
  and run comparison, version-aware reconciliation, compatibility gating), and
  ``honbu.materialize`` (packaged starter-tree materialization and drift).

Everyday schema, marker, hash, and error symbols are re-exported here; lower-level
walker, target, IO, and template internals live in ``honbu.core.*``, and flow and
peer areas stay behind explicit imports so importing ``honbu`` never pulls a layer
a project did not ask for.
"""

from honbu.core.blocks import (
    ExperimentConfig,
    HashedRuntimeConfig,
    OutputBlock,
    OutputLeaf,
    RuntimeConfig,
    validate_root_contract,
)
from honbu.core.hashes import (
    ByteHash,
    FannedHash,
    FieldHash,
    HashEntry,
    HashRegistry,
    SourceHash,
    project_fields,
    project_model,
    validate_identity_fields,
)
from honbu.core.markers import (
    DERIVED,
    DIRECTIVE,
    IDENTITY,
    PORTABLE_PATH,
    RESOLVED_ONLY,
    TEMPLATED,
    FromHash,
    PortablePath,
    ResolvedPath,
    ResolvedString,
    TemplatedIdentity,
    TemplatedString,
    unmarked_path_fields,
)
from honbu.core.model import DEFER, Deferred, StrictModel, backend_kwargs
from honbu.errors import (
    CanonicalizationError,
    DeclarationError,
    HonbuError,
    RefusalError,
    TemplateError,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "ByteHash",
    "CanonicalizationError",
    "DEFER",
    "DERIVED",
    "DIRECTIVE",
    "DeclarationError",
    "Deferred",
    "ExperimentConfig",
    "FannedHash",
    "FieldHash",
    "FromHash",
    "HashEntry",
    "HashRegistry",
    "HashedRuntimeConfig",
    "HonbuError",
    "IDENTITY",
    "OutputBlock",
    "OutputLeaf",
    "PORTABLE_PATH",
    "PortablePath",
    "RESOLVED_ONLY",
    "RefusalError",
    "ResolvedPath",
    "ResolvedString",
    "RuntimeConfig",
    "SourceHash",
    "StrictModel",
    "TEMPLATED",
    "TemplateError",
    "TemplatedIdentity",
    "TemplatedString",
    "__version__",
    "backend_kwargs",
    "project_fields",
    "project_model",
    "unmarked_path_fields",
    "validate_identity_fields",
    "validate_root_contract",
]
