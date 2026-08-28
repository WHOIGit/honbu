"""Honbu — the headquarters that defines the forms.

A framework for run-once, config-driven, content-addressed supervised training
runs of the shape ``config x fixed-dataset -> artifacts + metrics``. The
consuming project holds the main loop and reaches into Honbu; Honbu never inverts
control, so a project adopts the depth it wants and hand-rolls the rest.

Three layers. One install carries all of them; a project opts out of a layer by
not importing it (D20). The numbering is a dependency ordering, not a stack of
altitudes — composition and identity in particular are siblings, neither importing
the other, and resolution is the first thing that needs both.

* **Core** — the canonical encoder, hash declarations and
  artifacts, the strict model base, field markers and walkers, atomic writes,
  path portability, template rendering, collision-safe targets.
* **L2 flow** — declared commands and their write scopes, the fixed
  resolution phase order, collision safety and resume, the provenance bundle, the
  live run manifest, and the CLI grammar.
* **Operations** — sweeps, run comparison, ensemble assessment, and
  configuration materialization.

L1 and L2 are complete. L3 is under construction: sweep definition,
preparation, and local resumable execution exist; comparison and the remaining
cross-run capabilities do not yet.

The core is re-exported here for convenience. ``honbu.flow``, ``honbu.sweeps``,
``honbu.compare``, and ``honbu.materialize`` are not, so importing Honbu never pulls a layer a project
did not ask for.
"""

from honbu.errors import (
    CanonicalizationError,
    DeclarationError,
    HonbuError,
    RefusalError,
    TemplateError,
)
from honbu.core import *  # noqa: F403
from honbu.core import __all__ as _core_all

__version__ = "0.1.0.dev0"

__all__ = [
    "CanonicalizationError",
    "DeclarationError",
    "HonbuError",
    "RefusalError",
    "TemplateError",
    "__version__",
    *_core_all,
]
