"""L3 — capabilities built on the flow: sweeps, comparison, materialization.

Nothing here is required to run a command. A project that resolves a
configuration, writes a run and stops never imports this package; L3 exists for
the operations that span *several* runs, or that read a configuration without
running one at all.

The dependency direction is one-way and asserted by test: capabilities import
mechanics and flow, and neither imports back.
"""

from honbu.capabilities.sweep import (
    Axis,
    AxisOption,
    Coordinate,
    SweepBlock,
    axis_kind,
    expand,
    normalize_axes,
    sweep_axes,
    sweep_block_of,
    sweep_definition_digest,
    without_sweep_block,
)

__all__ = [
    "Axis",
    "AxisOption",
    "Coordinate",
    "SweepBlock",
    "axis_kind",
    "expand",
    "normalize_axes",
    "sweep_axes",
    "sweep_block_of",
    "sweep_definition_digest",
    "without_sweep_block",
]
