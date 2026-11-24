"""Configuration-tree materialization and drift operations (D27)."""

from honbu.materialize.trees import (
    DEFAULT_PLUMBING_EXCLUDES,
    FileDrift,
    MaterializationAction,
    MaterializationPlan,
    MaterializationRoot,
    MaterializationScope,
    MaterializedFile,
    apply_materialization,
    closure_for_root,
    closure_roots,
    compare_materialized_config,
    materialized_file_drift,
    plan_materialization,
    render_file_drift,
    render_plan,
)

__all__ = [
    "DEFAULT_PLUMBING_EXCLUDES",
    "FileDrift",
    "MaterializationAction",
    "MaterializationPlan",
    "MaterializationRoot",
    "MaterializationScope",
    "MaterializedFile",
    "apply_materialization",
    "closure_for_root",
    "closure_roots",
    "compare_materialized_config",
    "materialized_file_drift",
    "plan_materialization",
    "render_file_drift",
    "render_plan",
]
