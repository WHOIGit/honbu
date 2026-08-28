"""Configuration-tree materialization operations."""

from honbu.materialize.trees import (
    MaterializationPlan,
    MaterializationRoot,
    MaterializationScope,
    MaterializedFile,
    apply_materialization,
    compare_materialized_config,
    closure_roots,
    closure_for_root,
    plan_config_materialization,
    plan_materialization,
)

__all__ = [
    "MaterializationPlan",
    "MaterializationRoot",
    "MaterializationScope",
    "MaterializedFile",
    "apply_materialization",
    "compare_materialized_config",
    "closure_roots",
    "closure_for_root",
    "plan_config_materialization",
    "plan_materialization",
]
