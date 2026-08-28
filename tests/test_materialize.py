"""Materialization plans are additive and explicit about every file."""

from __future__ import annotations

from honbu.materialize import (
    MaterializationScope,
    MaterializationRoot,
    apply_materialization,
    compare_materialized_config,
    closure_roots,
    plan_config_materialization,
    plan_materialization,
)
from honbu.flow import ComposeSpec


def test_closure_roots_uses_hydras_choices_and_excludes_hydra_itself():
    assert closure_roots(
        config_name="config",
        choices={"model": "small", "hydra/job_logging": "default"},
    ) == ("config.yaml", "model/small.yaml")


def test_plan_then_apply_is_additive_and_overwrite_is_explicit(tmp_path):
    packaged = tmp_path / "packaged"
    (packaged / "model").mkdir(parents=True)
    (packaged / "config.yaml").write_text("defaults: []\n")
    (packaged / "model" / "small.yaml").write_text("width: 16\n")
    destination = tmp_path / "local"
    scope = MaterializationScope("starter", roots=("config.yaml", "model"))

    plan = plan_materialization(packaged, destination, scopes={"starter": scope}, defaults=("starter",))
    assert {item.action for item in plan.files} == {"create"}
    apply_materialization(plan)
    assert (destination / "model" / "small.yaml").is_file()

    kept = plan_materialization(packaged, destination, scopes={"starter": scope}, defaults=("starter",))
    assert {item.action for item in kept.files} == {"skip"}
    overwritten = plan_materialization(
        packaged, destination, scopes={"starter": scope}, defaults=("starter",), overwrite=True
    )
    assert {item.action for item in overwritten.files} == {"overwrite"}


def test_scoped_config_materialization_uses_compositors_transitive_choices(tmp_path):
    packaged = tmp_path / "packaged"
    (packaged / "model").mkdir(parents=True)
    (packaged / "config.yaml").write_text("defaults:\n  - model: small\n  - _self_\n")
    (packaged / "model" / "small.yaml").write_text("width: 16\n")
    destination = tmp_path / "local"
    plan = plan_config_materialization(
        packaged,
        destination,
        compose_spec=ComposeSpec(packaged=None, local=str(packaged)),
        scopes={
            "starter": MaterializationScope(
                "starter", config_roots=(MaterializationRoot("config"),)
            )
        },
        defaults=("starter",),
        cwd=tmp_path,
    )
    assert [item.destination.relative_to(destination).as_posix() for item in plan.files] == [
        "config.yaml",
        "model/small.yaml",
    ]


def test_materialized_drift_reuses_configuration_comparison(tmp_path, monkeypatch):
    package = tmp_path / "starter"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "config.yaml").write_text("epochs: 10\n")
    local = tmp_path / "local"
    local.mkdir()
    (local / "config.yaml").write_text("epochs: 20\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    result = compare_materialized_config(
        local,
        compose_spec=ComposeSpec(packaged="starter"),
        cwd=tmp_path,
    )
    assert result.labels == ("packaged", "local")
    assert result.differences[0].path == "epochs"
