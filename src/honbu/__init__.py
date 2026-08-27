"""Honbu — the headquarters that defines the forms.

A framework for run-once, config-driven, content-addressed supervised training
runs of the shape ``config x fixed-dataset -> artifacts + metrics``. The
consuming project holds the main loop and reaches into Honbu; Honbu never inverts
control, so a project adopts the depth it wants and hand-rolls the rest.

Three layers, each opt-in below the first:

* **L1 mechanics** (always) — the canonical encoder, hash declarations and
  artifacts, the strict model base, field markers and walkers, atomic writes,
  path portability, template rendering, collision-safe targets.
* **L2 flow** (opt-in) — declared commands and their write scopes, the fixed
  resolution phase order, collision safety and resume, the provenance bundle, the
  live run manifest, and the CLI grammar.
* **L3 capabilities** (opt-in) — sweeps, run comparison, ensemble assessment.

L1 is complete. L2 has its command declarations and run manifest; composition and
the resolver are still to come. L3 does not exist yet.
"""

from honbu.errors import (
    CanonicalizationError,
    DeclarationError,
    HonbuError,
    RefusalError,
    TemplateError,
)
from honbu.mechanics import *  # noqa: F403
from honbu.mechanics import __all__ as _mechanics_all

__version__ = "0.1.0.dev0"

__all__ = [
    "CanonicalizationError",
    "DeclarationError",
    "HonbuError",
    "RefusalError",
    "TemplateError",
    "__version__",
    *_mechanics_all,
]
