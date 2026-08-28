"""The frozen canonical encoding every Honbu digest is defined over.

Two structurally equal sources MUST produce identical bytes in every consumer, so
this module takes no options, has no hooks, and refuses anything it cannot encode
unambiguously rather than coercing it. The refusals are the design:

* **Non-string mapping keys** are refused, never stringified. Coercing would make
  ``{1: "a"}`` and ``{"1": "a"}`` share a digest — two different configurations,
  one identity.
* **Non-finite floats** are refused. A NaN in a configuration is a bug, not a
  value, and refusing it removes the need for a tokenization scheme (and the
  reserved key namespace such a scheme would cost).
* **Anything outside null / str / int / bool / float / mapping / sequence** is
  refused by type name and dotted path, rather than being handed to ``json.dumps``
  to fail somewhere unhelpful. This is what turns a stray ``numpy.int64`` from a
  confusing traceback into ``annotations.14.class_id: int64 is not encodable``.

Normalizations, all of them deliberate: keys sort, so authored order is
irrelevant; floats round to twelve significant digits, so floating-point noise
does not move an identity; negative zero folds to zero, because the sign of zero
is never behavior; strings are UTF-8 with **no** Unicode normalization, because a
digest should mean "these bytes".
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Final, NoReturn

from honbu.errors import CanonicalizationError

#: Bumping this invalidates every digest Honbu has ever produced. It is recorded
#: in each hash artifact and in the run manifest, and appears in no digest and in
#: no resolved configuration. Changing it is a major framework version that bumps
#: every hash recipe version.
CANONICAL_ENCODER_VERSION: Final = "1"

_FLOAT_SIGNIFICANT_DIGITS: Final = 12


def _fail(path: tuple[str, ...], message: str) -> NoReturn:
    raise CanonicalizationError(f"{'.'.join(path) or '<root>'}: {message}")


def _canonical_float(value: float, path: tuple[str, ...]) -> float:
    if math.isnan(value) or math.isinf(value):
        _fail(path, f"non-finite float ({value}) cannot carry identity")
    normalized = float(f"{value:.{_FLOAT_SIGNIFICANT_DIGITS}g}")
    # -0.0 folds to 0.0: the sign of zero is never behavior.
    return 0.0 if normalized == 0.0 else normalized


def _canonical_string(value: str, path: tuple[str, ...]) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        # json.dumps(ensure_ascii=False) emits lone surrogates happily; the
        # failure would otherwise surface at .encode() with no field to blame.
        _fail(path, f"string is not UTF-8 encodable ({error.reason})")
    return value


def _canonicalize(value: Any, path: tuple[str, ...]) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _canonical_string(value, path)
    # bool before int: bool subclasses int, and `true` is not `1` in the encoding.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _canonical_float(value, path)
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                _fail(path, f"mapping key {key!r} is {type(key).__name__}, not str")
        return {key: _canonicalize(value[key], (*path, key)) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item, (*path, str(index))) for index, item in enumerate(value)]
    # Sets land here on purpose: they have no canonical order, so a project that
    # wants order-insensitive identity must sort into a list deliberately.
    _fail(path, f"{type(value).__name__} is not canonically encodable")


def canonical_bytes(source: Any) -> bytes:
    """The canonical UTF-8 encoding of ``source``. The only thing ever hashed."""
    return json.dumps(
        _canonicalize(source, ()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_text(source: Any) -> str:
    """The canonical encoding as text, for diffing and error messages."""
    return canonical_bytes(source).decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_source(source: Mapping[str, Any]) -> str:
    """Digest a declared hash source. Sources are always mappings — bare namespace."""
    return sha256_bytes(canonical_bytes(source))


def file_digest(path: Any, *, chunk_size: int = 1 << 20) -> str:
    """Digest a file's bytes. Used by byte-mode hashes, which have no source."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
