"""The strict model base every Honbu and project configuration block inherits.

Three decisions are recorded here because they are load-bearing, non-obvious, and
expensive to rediscover:

1. ``model_fields_set`` is NOT usable anywhere in Honbu or a consuming project.
   Resolution assigns to fields (which adds them to the set) and ends with a
   ``model_dump()`` → ``model_validate()`` round trip (which marks every field as
   explicitly set). Both destroy the authored/defaulted distinction. The record of
   what a user authored is the composed configuration the provenance bundle
   persists — not this attribute.

2. Absence never carries meaning. ``None`` is a real value. A block that needs
   "let the backend library apply its own default" says so with :data:`DEFER`,
   which survives ``exclude_none`` and therefore appears in both the resolved
   configuration and the hash source. A run that deferred and a run that pinned
   the same number are different runs and must not share a digest.

3. Arbitrary types stay forbidden. Every field must dump to a JSON-native value
   under ``mode="json"``, because that dump is the input to the canonical encoder.
   Admitting an opaque type would make some configurations unhashable at runtime
   rather than at import.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

#: "Omit this knob so the backend library applies its own default."
#:
#: Prefer pinning to deferring. Two runs a year apart that both defer carry the
#: same digest and can behave differently, because the digest records the
#: deferral, not the value the library chose.
DEFER: Final = "DEFER"
Deferred = Literal["DEFER"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        # A typo'd key fails at load instead of being silently ignored. This is
        # also what makes a mistyped composition override an error, not a no-op.
        extra="forbid",
        # Resolution mutates the tree in place, so assignment must be checked.
        # Note this re-runs the validators of the model *owning* the field, never
        # the root's — a root-level cross-field rule needs the final round trip.
        validate_assignment=True,
        # Defaults and default_factory results are validated too. Pydantic skips
        # them by default, which lets a block ship a default violating its own
        # constraint and fail only for the one caller who overrides nothing.
        validate_default=True,
        # An invisible trailing space must not produce a different digest and a
        # different run directory. Opt a single field out with
        # Annotated[str, StringConstraints(strip_whitespace=False)]; opting a
        # whole block out via ConfigDict works but hides the exemption from the
        # field that needs it.
        str_strip_whitespace=True,
        # Non-finite floats survive the dump as floats instead of becoming None.
        # Pydantic's default ('null') would turn `inf` into None, which
        # exclude_none then drops — silently erasing the field from the digest.
        # As 'constants' it reaches the canonical encoder, which refuses it and
        # names the offending path.
        ser_json_inf_nan="constants",
        # See decision 3 above.
        arbitrary_types_allowed=False,
    )


def _omit_deferred(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _omit_deferred(v) for k, v in value.items() if v != DEFER}
    if isinstance(value, list):
        return [_omit_deferred(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_omit_deferred(v) for v in value)
    return value


def backend_kwargs(
    block: BaseModel,
    *,
    mode: Literal["python", "json"] = "python",
    recursive: bool = False,
) -> dict[str, Any]:
    """Forwardable knobs from ``block``, with deferred ones omitted.

    One framework helper instead of a hand-rolled filter per block, so "what does
    DEFER mean at the call site" has exactly one answer. Filters by value rather
    than by ``model_fields_set``, per decision 1.

    Defaults to ``mode="python"`` so native Python types such as ``tuple`` and
    ``Path`` survive intact when forwarded directly to Python backend libraries;
    pass ``mode="json"`` for a JSON-native mapping, and ``recursive=True`` to
    also omit ``DEFER`` inside nested mappings.
    """
    dumped = block.model_dump(mode=mode)
    if recursive:
        return _omit_deferred(dumped)
    return {name: value for name, value in dumped.items() if value != DEFER}
