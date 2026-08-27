"""Honbu's error taxonomy.

The distinction that matters to a caller is whether an error is *for the user*.
A ``RefusalError`` is a decision Honbu made on purpose — a non-empty output
target, an unresolvable template, a path that would not survive the filesystem —
and the command harness prints its message and exits non-zero with no traceback.
Everything else is a bug and should look like one.
"""

from __future__ import annotations


class HonbuError(Exception):
    """Base for every error Honbu raises deliberately."""


class RefusalError(HonbuError):
    """A user-facing refusal. Printed and exited on; never a traceback."""


class CanonicalizationError(HonbuError):
    """A value cannot be canonically encoded. Carries the offending dotted path."""


class TemplateError(HonbuError):
    """A template references something unavailable, unregistered, or unusable."""


class DeclarationError(HonbuError):
    """A hash or schema declaration is inconsistent. Raised at import time."""
