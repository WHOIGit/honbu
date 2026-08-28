"""The layering guard.

D20 put Hydra, OmegaConf and PyYAML in the core dependencies, which removed the
one thing that made the mechanics/flow boundary structural: those packages were
previously in an extra, so a mechanics module *could not* import them even by
accident. This file is what replaces that guarantee.

It is deliberately an AST scan rather than an import-time check. Asking "did
importing honbu.mechanics pull in omegaconf" would pass a module that imports it
lazily inside a function, which is exactly the shape a convenience import takes.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "honbu"
MECHANICS = SRC / "mechanics"
FLOW = SRC / "flow"

#: Packages the mechanics layer must stay clear of. Not a style rule: the layer's
#: whole claim is that it works on a dict you built by hand, and one import of a
#: composition primitive ends that claim silently.
FORBIDDEN = frozenset({"hydra", "omegaconf", "yaml"})


def _imported_roots(source: str) -> set[str]:
    """Every top-level package name imported anywhere in the module.

    Includes imports nested inside functions and conditionals, which is the point:
    a lazy import is still a dependency.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_mechanics_imports_no_composition_primitive():
    offenders: dict[str, set[str]] = {}
    for path in sorted(MECHANICS.rglob("*.py")):
        forbidden = _imported_roots(path.read_text()) & FORBIDDEN
        if forbidden:
            offenders[path.name] = forbidden
    assert not offenders, (
        f"mechanics modules import forbidden packages: {offenders}. The mechanics "
        "layer must be usable without a compositor; move whatever needs one into flow."
    )


def test_the_scan_would_catch_a_lazy_import():
    """Guard the guard: a function-local import must not slip through."""
    assert _imported_roots("def f():\n    import omegaconf\n") == {"omegaconf"}


def _honbu_imports(source: str) -> set[str]:
    """Every ``honbu.*`` module a source file imports, lazily or otherwise."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        module = (
            node.module if isinstance(node, ast.ImportFrom) and node.level == 0 else None
        )
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
        found.update(c for c in filter(None, [module, *names]) if c.startswith("honbu."))
    return found


def test_mechanics_is_importable_without_touching_flow():
    """The layer boundary in the other direction: importing the mechanics must not
    drag in the flow package, or 'take only the mechanics' would be untrue."""
    for path in sorted(MECHANICS.rglob("*.py")):
        for candidate in _honbu_imports(path.read_text()):
            assert not candidate.startswith("honbu.flow"), (
                f"{path.name} imports {candidate}; the mechanics layer cannot "
                "depend on the flow above it"
            )


def test_nothing_below_the_capabilities_reaches_up_into_them():
    """L3 is optional in the only sense that matters: nothing below needs it.

    A sweep, a comparison and a materialization all span more than one run, or read
    a configuration without running one. If flow reached into them, resolving a
    configuration would drag in the whole of L3, and 'adopt the depth you want'
    would be a claim with nothing behind it.
    """
    for path in sorted([*MECHANICS.rglob("*.py"), *FLOW.rglob("*.py")]):
        for candidate in _honbu_imports(path.read_text()):
            assert not candidate.startswith("honbu.capabilities"), (
                f"{path.relative_to(SRC)} imports {candidate}; the capabilities are "
                "built on the layers below and cannot be depended on by them"
            )
