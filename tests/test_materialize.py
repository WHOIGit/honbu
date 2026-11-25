"""Materialization plans, configs-only closures, dual drift views, and CLI builder (D27)."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import Field
from typer.testing import CliRunner

from honbu import ExperimentConfig, FieldHash, HashRegistry, OutputBlock, PortablePath, RuntimeConfig, StrictModel
from honbu.cli.presets import build_materialize_command
from honbu.flow import ComposeSpec, ProjectSpec
from honbu.materialize import (
    MaterializationRoot,
    MaterializationScope,
    apply_materialization,
    closure_for_root,
    closure_roots,
    compare_materialized_config,
    materialized_file_drift,
    plan_materialization,
    render_file_drift,
    render_plan,
)


def _make_packaged_tree(root: Path, pkg_name: str = "demo_pkg") -> Path:
    pkg = root / pkg_name
    (pkg / "model").mkdir(parents=True)
    (pkg / "backbone" / "torchvision").mkdir(parents=True)
    (pkg / "data").mkdir(parents=True)
    (pkg / "styles").mkdir(parents=True)
    (pkg / "__pycache__").mkdir(parents=True)

    (pkg / "__init__.py").write_text("")
    (pkg / "__pycache__" / "__init__.cpython-313.pyc").write_bytes(b"bytecode")
    (pkg / "model" / "__init__.py").write_text("")
    (pkg / "AGENTS.md").write_text("internal note\n")
    (pkg / "styles" / "plot.toml").write_text("dpi = 150\n")

    (pkg / "config.yaml").write_text(
        "defaults:\n"
        "  - _self_\n"
        "  - data: local\n"
        "  - model: small\n"
        "  - /backbone/torchvision@model.backbone: efficientnet_b0\n"
        "experiment:\n"
        "  name: demo\n"
        "epochs: 10\n"
    )
    (pkg / "data" / "local.yaml").write_text("uri: ./data\n")
    (pkg / "data" / "remote.yaml").write_text("uri: s3://bucket/data\n")
    (pkg / "model" / "small.yaml").write_text("width: 16\n")
    (pkg / "model" / "large.yaml").write_text("width: 64\n")
    (pkg / "backbone" / "torchvision" / "efficientnet_b0.yaml").write_text(
        "arch: efficientnet_b0\n"
    )
    return pkg


def test_closure_roots_strips_package_suffix_and_excludes_hydra_internals():
    assert closure_roots(
        config_name="config",
        choices={
            "model": "small",
            "/backbone/torchvision@model.backbone": "efficientnet_b0",
            "hydra/job_logging": "default",
            "optional": "None",
        },
    ) == (
        "config.yaml",
        "model/small.yaml",
        "backbone/torchvision/efficientnet_b0.yaml",
    )


def test_closure_for_external_file_copies_only_packaged_dependencies(tmp_path, monkeypatch):
    _make_packaged_tree(tmp_path, "ext_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))

    authored = tmp_path / "my_experiment.yaml"
    authored.write_text(
        "defaults:\n"
        "  - _self_\n"
        "  - data: remote\n"
        "  - /backbone/torchvision@model.backbone: efficientnet_b0\n"
        "epochs: 25\n"
    )
    spec = ComposeSpec(packaged="ext_pkg")
    closure = closure_for_root(MaterializationRoot.from_file(authored), compose_spec=spec)
    assert closure == (
        "backbone/torchvision/efficientnet_b0.yaml",
        "data/remote.yaml",
    )
    assert "my_experiment.yaml" not in closure


def test_unified_plan_materialization_scopes_plumbing_exclusions_and_render(
    tmp_path, monkeypatch
):
    _make_packaged_tree(tmp_path, "plan_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))

    destination = tmp_path / "configs"
    spec = ComposeSpec(packaged="plan_pkg")
    scopes = {
        "starter": MaterializationScope(
            "starter",
            roots=(MaterializationRoot("config"),),
            files=("styles/plot.toml",),
            exclude=("AGENTS.md",),
        ),
        "all_tree": MaterializationScope(
            "all_tree",
            files=(".",),
            exclude=("AGENTS.md",),
        ),
    }

    plan = plan_materialization(
        destination, compose_spec=spec, scopes=scopes, defaults=("starter",)
    )
    rel_paths = [item.destination.relative_to(destination).as_posix() for item in plan.files]
    assert rel_paths == [
        "backbone/torchvision/efficientnet_b0.yaml",
        "config.yaml",
        "data/local.yaml",
        "model/small.yaml",
        "styles/plot.toml",
    ]
    assert len(plan.created) == 5
    assert plan.skipped == ()
    assert plan.overwritten == ()
    rendered = render_plan(plan)
    assert "5 create, 0 skip, 0 overwrite" in rendered
    assert "styles/plot.toml" in rendered

    # Apply creates files; local-only files survive; re-planning skips existing
    apply_materialization(plan)
    (destination / "custom_local.yaml").write_text("mine: true\n")
    kept = plan_materialization(
        destination, compose_spec=spec, scopes=scopes, defaults=("starter",)
    )
    assert len(kept.skipped) == 5
    assert kept.created == ()

    overwritten = plan_materialization(
        destination,
        compose_spec=spec,
        scopes=scopes,
        defaults=("starter",),
        overwrite=True,
    )
    assert len(overwritten.overwritten) == 5
    apply_materialization(overwritten)
    assert (destination / "custom_local.yaml").read_text() == "mine: true\n"

    # Directory walk excludes __init__.py, __pycache__/*.pyc, and AGENTS.md
    full_plan = plan_materialization(
        tmp_path / "full_out", compose_spec=spec, scopes=scopes, selected=("all_tree",)
    )
    full_rels = {
        item.destination.relative_to(tmp_path / "full_out").as_posix()
        for item in full_plan.files
    }
    assert "__init__.py" not in full_rels
    assert "model/__init__.py" not in full_rels
    assert "AGENTS.md" not in full_rels
    assert not any("__pycache__" in p or p.endswith(".pyc") for p in full_rels)
    assert "styles/plot.toml" in full_rels


def test_composed_drift_supports_partial_local_trees_and_overrides(tmp_path, monkeypatch):
    _make_packaged_tree(tmp_path, "drift_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))

    spec = ComposeSpec(packaged="drift_pkg")

    # Partial local tree WITHOUT config.yaml — only overrides model/small.yaml!
    partial_no_root = tmp_path / "local_partial"
    (partial_no_root / "model").mkdir(parents=True)
    (partial_no_root / "model" / "small.yaml").write_text("width: 32\n")

    result = compare_materialized_config(partial_no_root, compose_spec=spec, cwd=tmp_path)
    assert result.labels == ("packaged", "local")
    assert [(d.path, d.values) for d in result.differences] == [("model.width", (16, 32))]

    # Overrides check a non-default group cleanly (data=remote has no local override -> equal!)
    (partial_no_root / "model" / "large.yaml").write_text("width: 128\n")
    overridden = compare_materialized_config(
        partial_no_root,
        compose_spec=spec,
        overrides=("model=large", "data=remote"),
        cwd=tmp_path,
    )
    assert [(d.path, d.values) for d in overridden.differences] == [("model.width", (64, 128))]


def test_file_level_drift_classifies_all_four_buckets(tmp_path, monkeypatch):
    _make_packaged_tree(tmp_path, "fdrift_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))

    spec = ComposeSpec(packaged="fdrift_pkg")
    local = tmp_path / "local_cfg"
    (local / "model").mkdir(parents=True)
    (local / "config.yaml").write_text(
        (tmp_path / "fdrift_pkg" / "config.yaml").read_text()
    )  # unchanged
    (local / "model" / "small.yaml").write_text("width: 99\n")  # modified
    (local / "custom.yaml").write_text("extra: 1\n")  # local_only

    drift = materialized_file_drift(local, compose_spec=spec, exclude=("AGENTS.md",))
    assert drift.has_drift is True
    assert drift.modified == ("model/small.yaml",)
    assert drift.unchanged == ("config.yaml",)
    assert drift.local_only == ("custom.yaml",)
    assert "data/local.yaml" in drift.packaged_only
    assert "__init__.py" not in drift.packaged_only
    assert "AGENTS.md" not in drift.packaged_only

    text = render_file_drift(drift)
    assert "modified" in text and "model/small.yaml" in text
    assert "local_only" in text and "custom.yaml" in text


def test_typer_materialize_command_builder_with_project_spec(tmp_path, monkeypatch):
    _make_packaged_tree(tmp_path, "cli_mat_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))

    class Data(StrictModel):
        uri: PortablePath

    class Model(StrictModel):
        width: int = 16
        backbone: dict = Field(default_factory=dict)

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: PortablePath = Path("./runs")
        outputs: OutputBlock = Field(default_factory=OutputBlock)
        data: Data
        model: Model
        epochs: int = 10

    project = ProjectSpec(
        root_type=Root,
        compose_spec=ComposeSpec(packaged="cli_mat_pkg"),
        hashes=HashRegistry([FieldHash("config_hash", include=["experiment", "epochs"])]),
    )
    scopes = {
        "starter": MaterializationScope("starter", roots=(MaterializationRoot("config"),))
    }
    app = typer.Typer()
    app.command("init-configs")(
        build_materialize_command(
            project=project,
            scopes=scopes,
            defaults=("starter",),
            default_destination=tmp_path / "out_cfg",
        )
    )
    runner = CliRunner()

    dry = runner.invoke(app, ["--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "create" in dry.output
    assert not (tmp_path / "out_cfg").exists()

    applied = runner.invoke(app, [])
    assert applied.exit_code == 0, applied.output
    assert (tmp_path / "out_cfg" / "config.yaml").is_file()
    assert (tmp_path / "out_cfg" / "backbone" / "torchvision" / "efficientnet_b0.yaml").is_file()


# --------------------------------------------------------------------------- #
# from_file: transitive local shadow, local-only skip, --clobber and --from
# --------------------------------------------------------------------------- #


def _local_edit_scenario(tmp_path, monkeypatch, pkg_name):
    """A materialized local tree whose edits now reference new options.

    ``configs/config.yaml`` selects ``model: small`` (materialized, and edited to
    pull in the packaged backbone) plus a custom local-only ``data: mine``.
    """
    _make_packaged_tree(tmp_path, pkg_name)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "configs"
    (local / "model").mkdir(parents=True)
    (local / "data").mkdir()
    (local / "config.yaml").write_text(
        "defaults:\n  - _self_\n  - data: mine\n  - model: small\nexperiment:\n  name: edited\n"
    )
    (local / "data" / "mine.yaml").write_text("uri: ./mine\n")
    # A transitive edit: the local group file now pulls an unmaterialized option.
    (local / "model" / "small.yaml").write_text(
        "defaults:\n  - /backbone/torchvision@backbone: efficientnet_b0\nwidth: 8\n"
    )
    return local


def test_from_file_honors_transitive_local_edits_and_skips_local_only(
    tmp_path, monkeypatch
):
    local = _local_edit_scenario(tmp_path, monkeypatch, "shadow_pkg")
    spec = ComposeSpec(packaged="shadow_pkg")
    root = MaterializationRoot.from_file(local / "config.yaml")

    closure = closure_for_root(root, compose_spec=spec)
    assert "backbone/torchvision/efficientnet_b0.yaml" in closure
    assert "data/mine.yaml" in closure  # selected; filtered by the planner

    plan = plan_materialization(
        local,
        scopes={"edit": MaterializationScope("edit", roots=(root,))},
        selected=("edit",),
        compose_spec=spec,
    )
    actions = {
        item.destination.relative_to(local).as_posix(): item.action for item in plan.files
    }
    assert actions == {
        "backbone/torchvision/efficientnet_b0.yaml": "create",
        "model/small.yaml": "skip",
    }
    apply_materialization(plan)
    # The edited local file is never replaced, and the custom file is untouched.
    assert "width: 8" in (local / "model" / "small.yaml").read_text()
    assert (local / "data" / "mine.yaml").read_text() == "uri: ./mine\n"
    assert (local / "backbone" / "torchvision" / "efficientnet_b0.yaml").is_file()


def test_packaged_roots_still_refuse_missing_packaged_files(tmp_path, monkeypatch):
    import pytest

    from honbu.errors import DeclarationError

    _make_packaged_tree(tmp_path, "strict_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    pkg_copy = tmp_path / "unpacked"
    pkg_copy.mkdir()
    (pkg_copy / "config.yaml").write_text("x: 1\n")  # lacks the closure's group files
    with pytest.raises(DeclarationError, match="closure names no packaged file"):
        plan_materialization(
            tmp_path / "out",
            scopes={"s": MaterializationScope("s", roots=("config",))},
            selected=("s",),
            compose_spec=ComposeSpec(packaged="strict_pkg"),
            packaged_root=pkg_copy,
        )


def _materialize_app(tmp_path, pkg_name, **kwargs):
    app = typer.Typer()
    app.command("init")(
        build_materialize_command(
            compose_spec=ComposeSpec(packaged=pkg_name),
            scopes={
                "starter": MaterializationScope(
                    "starter", roots=(MaterializationRoot("config"),)
                )
            },
            defaults=("starter",),
            default_destination=tmp_path / "configs",
            **kwargs,
        )
    )
    return app


def test_typer_materialize_command_clobber_replaces_overwrite(tmp_path, monkeypatch):
    _make_packaged_tree(tmp_path, "clobber_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    app = _materialize_app(tmp_path, "clobber_pkg")
    runner = CliRunner()

    assert runner.invoke(app, []).exit_code == 0
    edited = tmp_path / "configs" / "model" / "small.yaml"
    edited.write_text("width: 99\n")

    kept = runner.invoke(app, [])
    assert kept.exit_code == 0, kept.output
    assert edited.read_text() == "width: 99\n"

    legacy = runner.invoke(app, ["--overwrite"])
    assert legacy.exit_code != 0

    clobbered = runner.invoke(app, ["--clobber"])
    assert clobbered.exit_code == 0, clobbered.output
    assert "overwrite" in clobbered.output
    assert edited.read_text() == "width: 16\n"


def test_typer_materialize_command_from_file(tmp_path, monkeypatch):
    local = _local_edit_scenario(tmp_path, monkeypatch, "from_pkg")
    app = _materialize_app(tmp_path, "from_pkg")
    runner = CliRunner()

    dry = runner.invoke(app, ["--from", str(local / "config.yaml"), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "1 create, 1 skip" in dry.output
    assert "backbone/torchvision/efficientnet_b0.yaml" in dry.output
    # --from without --scope plans only that closure, never the defaults.
    assert "data/local.yaml" not in dry.output
    assert not (local / "backbone").exists()

    combined = runner.invoke(
        app, ["--from", str(local / "config.yaml"), "--scope", "starter", "--dry-run"]
    )
    assert combined.exit_code == 0, combined.output
    assert "data/local.yaml" in combined.output

    missing = runner.invoke(app, ["--from", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 1
    assert "does not exist" in missing.output
    assert "Traceback" not in missing.output
