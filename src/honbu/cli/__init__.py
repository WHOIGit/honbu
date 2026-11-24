"""Opt-in, library-agnostic CLI helpers (D19).

Projects fully own their CLI library, command tree, flag names, and help text.
This module exports reusable building blocks — dash-rule override validation,
flag-independent configuration source selection, refusal-versus-defect exit
handling, warning emission, and documented conventional flag names — with zero
Typer dependency.

For ready-made Typer option aliases and mountable command builders, import
:mod:`honbu.cli.presets`.
"""

from honbu.cli.helpers import (
    CONVENTIONAL_FLAGS,
    DashRuleSplit,
    emit_warnings,
    exit_on_refusal,
    select_config_source,
    split_dash_rule,
    validate_override_tokens,
)

__all__ = [
    "CONVENTIONAL_FLAGS",
    "DashRuleSplit",
    "emit_warnings",
    "exit_on_refusal",
    "select_config_source",
    "split_dash_rule",
    "validate_override_tokens",
]
