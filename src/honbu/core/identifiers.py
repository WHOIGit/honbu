"""Human-readable identities: deterministic from a value, or fresh per invocation.

A digest identifies a thing; a two-word slug lets you say it out loud. Both exist
for the same reason — ``runs/brave-otter/`` is a directory you can talk about and
``runs/sha256:8c1f…`` is not.

Deterministic identities are domain-seeded so the same digest labelled for two
purposes cannot collide into one name. Fresh identities are drawn from a
per-invocation seed the resolver generates once, which keeps rendering pure while
still giving two runs of the same composed configuration different directories.
"""

from __future__ import annotations

import hashlib
import random
import secrets
import threading

import coolname

_SLUG_WORDS = 2
_COOLNAME_LOCK = threading.Lock()


def _seeded_slug(domain: str, value: str) -> str:
    digest = hashlib.sha256(f"{domain}:{value}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    with _COOLNAME_LOCK:
        generator = coolname.generate.__self__
        previous = generator.random
        try:
            coolname.replace_random(rng)
            return coolname.generate_slug(_SLUG_WORDS)
        finally:
            coolname.replace_random(previous)


def deterministic_id(domain: str, value: str) -> str:
    """A stable two-word identity for ``value`` within ``domain``."""
    return _seeded_slug(domain, value)


def fresh_seed() -> str:
    """Per-invocation entropy. Generated once by the resolver, then passed around."""
    return secrets.token_hex(8)
