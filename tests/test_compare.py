"""Configuration comparison: levelled, schema-normalized, pure-hash evaluation (D26)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import (
    DERIVED,
    DIRECTIVE,
    PORTABLE_PATH,
    ByteHash,
    ExperimentConfig,
    FannedHash,
    FieldHash,
    HashRegistry,
    HashedRuntimeConfig,
    OutputBlock,
    PortablePath,
    RefusalError,
    SourceHash,
    StrictModel,
    project_model,
)
from honbu.compare import (
    ABSENT,
    ConfigOperand,
    compare_configs,
    compare_operands,
    render_comparison,
)
from honbu.flow import CommandSpec, ComposeSpec, ProjectSpec, TargetSpec, open_run, resolve


class Head(StrictModel):
    classes: int = 2
    manifest: PortablePath | None = None


class Model(StrictModel):
    name: str = "small"
    dropout: float = 0.1
    heads: dict[str, Head] = Field(default_factory=dict)


class Root(StrictModel):
    experiment: ExperimentConfig = Field(default_factory=lambda: ExperimentConfig(name="exp"))
    runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
    model: Model = Field(default_factory=Model)
    output_root: Annotated[Path, PORTABLE_PATH] = Path("./runs")
    outputs: OutputBlock = Field(default_factory=lambda: OutputBlock(dir_template="out/{runtime.run_id}"))
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    run_note: Annotated[str | None, DIRECTIVE] = None
    extra_id: Annotated[str | None, DERIVED] = None


FIRST = {"model": {"name": "small"}, "output_root": "./runs", "extra_id": "a"}
SECOND = {"model": {"name": "large"}, "output_root": "./elsewhere", "extra_id": "b"}


def test_literal_comparison_is_n_way_and_distinguishes_absence_null_and_empty_containers():
    result = compare_configs([{"value": None}, {}, {"value": 2}])
    assert result.differences[0].path == "value"
    assert result.differences[0].values == (None, ABSENT, 2)

    # Empty dicts and empty lists stay distinct from ABSENT (7.8)
    empty_cmp = compare_configs([{"tags": [], "meta": {}}, {}])
    by_path = {d.path: d.values for d in empty_cmp.differences}
    assert by_path["tags"] == ([], ABSENT)
    assert by_path["meta"] == ({}, ABSENT)


def test_schema_validation_normalizes_defaults_at_authored_behavioral_and_by_hash():
    # Operand A omits schema defaults (model.dropout, tags, experiment); Operand B writes them out
    authored = {"model": {"name": "small"}}
    explicit_defaults = {
        "experiment": {"name": "exp"},
        "runtime": {"seed": None, "run_id": "abc", "config_id": "cool-slug", "config_hash": "sha256:1"},
        "model": {"name": "small", "dropout": 0.1, "heads": {}},
        "output_root": "/abs/runs",
        "outputs": {"dir": "out/abc", "dir_template": "out/{runtime.run_id}"},
        "tags": [],
        "meta": {},
    }
    lit = compare_configs([authored, explicit_defaults], level="literal")
    assert len(lit.differences) > 0

    beh = compare_configs([authored, explicit_defaults], level="behavioral", root_type=Root)
    assert beh.differences == ()

    auth = compare_configs([authored, explicit_defaults], level="authored", root_type=Root)
    # Only authored path/template differences appear at authored; resolved-only and schema defaults match!
    auth_paths = {d.path for d in auth.differences}
    assert "model.dropout" not in auth_paths
    assert "runtime.config_hash" not in auth_paths


def test_behavioral_projection_and_residuals_follow_markers_including_hyphenated_keys():
    a = {
        "model": {
            "name": "small",
            "heads": {"my-head": {"classes": 3, "manifest": "/a/labels.csv"}},
        },
        "output_root": "./runs",
        "extra_id": "a",
    }
    b = {
        "model": {
            "name": "large",
            "heads": {"my-head": {"classes": 3, "manifest": "/b/labels.csv"}},
        },
        "output_root": "./elsewhere",
        "extra_id": "b",
    }
    result = compare_configs([a, b], level="behavioral", root_type=Root)
    assert [difference.path for difference in result.differences] == ["model.name"]
    assert "model.heads.my-head.manifest" in result.residual_paths
    assert "output_root" in result.residual_paths
    assert "extra_id" in result.residual_paths


def test_by_hash_evaluates_pure_field_source_and_fanned_hashes_and_keeps_input_reading_opaque():
    hashes = HashRegistry(
        [
            FieldHash("config_hash", include=["model"], version="1"),
            SourceHash(
                "model_config_hash",
                build=lambda ctx: {"version": "1", **project_model(ctx["config"].model)},
                reads_inputs=False,
            ),
            FannedHash(
                "head_hash",
                build=lambda ctx: {
                    name: {"version": "1", "classes": head.classes}
                    for name, head in ctx["config"].model.heads.items()
                },
                reads_inputs=False,
            ),
            SourceHash(
                "dataset_hash",
                build=lambda ctx: {"version": "1", "rows": 10},
                reads_inputs=True,
            ),
            ByteHash("weights_hash", locate=lambda ctx: None),
        ],
        root_type=Root,
    )
    a = {"model": {"name": "small", "heads": {"dorsal": {"classes": 3}, "caudal": {"classes": 5}}}}
    b = {"model": {"name": "small", "heads": {"dorsal": {"classes": 3}, "caudal": {"classes": 7}}}}

    result = compare_configs([a, b], level="by_hash", root_type=Root, hashes=hashes)
    by_name = {hc.name: hc for hc in result.hash_comparisons}
    assert set(by_name) == {"config_hash", "model_config_hash", "head_hash.dorsal", "head_hash.caudal"}
    assert by_name["head_hash.dorsal"].equal is True
    assert by_name["head_hash.caudal"].equal is False
    assert by_name["head_hash.caudal"].source_differences[0].path == "classes"
    assert by_name["model_config_hash"].equal is False
    # Only reads_inputs=True declarations are opaque!
    assert result.opaque_hashes == ("dataset_hash", "weights_hash")
    rendered = render_comparison(result)
    assert "head_hash.caudal: differs" in rendered
    assert "Opaque hashes: dataset_hash, weights_hash" in rendered


def test_by_hash_refuses_missing_command_for_command_scoped_field_hash():
    hashes = HashRegistry(
        [FieldHash("config_hash", include={"train": ["model"], "eval": ["experiment"]})],
        root_type=Root,
    )
    with pytest.raises(RefusalError, match="requires command"):
        compare_configs([FIRST, SECOND], level="by_hash", root_type=Root, hashes=hashes)

    res = compare_configs(
        [FIRST, SECOND], level="by_hash", root_type=Root, hashes=hashes, command="train"
    )
    assert res.differences[0].moved_hashes == ("config_hash",)


def test_unavailable_levels_refuse_instead_of_downgrading():
    with pytest.raises(RefusalError, match="root_type"):
        compare_configs([FIRST, SECOND], level="behavioral")
    with pytest.raises(RefusalError, match="hash registry"):
        compare_configs([FIRST, SECOND], level="by_hash", root_type=Root)


def test_operand_sources_extra_dirs_config_index_and_project_spec(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "config.yaml").write_text("model:\n  name: small\n")
    (tmp_path / "overlay" / "model").mkdir(parents=True)
    (tmp_path / "overlay" / "model" / "alt.yaml").write_text("name: alt\n")

    hashes = HashRegistry([FieldHash("config_hash", include=["model"])], root_type=Root)
    project = ProjectSpec(
        root_type=Root,
        compose_spec=ComposeSpec(packaged=None, local="configs"),
        hashes=hashes,
    )
    # Per-operand extra_dirs
    cmp = compare_operands(
        [
            ConfigOperand(label="base"),
            ConfigOperand(
                overrides=("+model=alt",),
                extra_dirs=(tmp_path / "overlay",),
                label="overlay",
            ),
        ],
        project=project,
        level="behavioral",
        cwd=tmp_path,
    )
    assert cmp.differences[0].path == "model.name"
    assert cmp.differences[0].values == ("small", "alt")

    # Run directory with composed / resolved and config_index=2
    target = TargetSpec("outputs.dir_template", "outputs.dir", "output_root")
    train_cmd = CommandSpec(name=("train",), target=target, hashes=("config_hash",))
    eval_cmd = CommandSpec(
        name=("eval",), mode="augment", target=target, config_sources=("resolved",), hashes=("config_hash",)
    )
    base_cfg = {"model": {"name": "small"}}
    res1 = resolve(base_cfg, command=train_cmd, project=project, cwd=tmp_path)
    with open_run(res1, composed=base_cfg, project=project) as run:
        run_dir = run.run_dir
    aug_cfg = {"model": {"name": "large"}}
    res2 = resolve(aug_cfg, command=eval_cmd, project=project, existing_target=run_dir, cwd=tmp_path)
    with open_run(res2, composed=aug_cfg, project=project):
        pass

    idx_cmp = compare_operands(
        [
            ConfigOperand(source="composed", reference=run_dir, config_index=1, label="i1"),
            ConfigOperand(source="resolved", reference=run_dir, config_index=2, label="i2"),
        ],
        project=project,
        level="behavioral",
        cwd=tmp_path,
    )
    assert idx_cmp.differences[0].path == "model.name"
    assert idx_cmp.differences[0].values == ("small", "large")


def test_group_fragment_refuses_marker_defined_comparison(tmp_path):
    fragment = tmp_path / "small.yaml"
    fragment.write_text("name: small\n")
    with pytest.raises(RefusalError, match="literal level"):
        compare_operands(
            [ConfigOperand(source="file", reference=fragment, fragment=True), ConfigOperand()],
            compose_spec=ComposeSpec(packaged=None),
            level="behavioral",
            root_type=Root,
            cwd=tmp_path,
        )


# --------------------------------------------------------------------------- #
# packaged operands and file operands backed by the search path
# --------------------------------------------------------------------------- #


def _packaged_with_local_shadow(tmp_path, monkeypatch, pkg_name):
    pkg = tmp_path / pkg_name
    (pkg / "model").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.yaml").write_text("defaults:\n  - _self_\n  - model: small\n")
    (pkg / "configA.yaml").write_text("defaults:\n  - _self_\n  - model: large\n")
    (pkg / "model" / "small.yaml").write_text("name: small\n")
    (pkg / "model" / "large.yaml").write_text("name: large\n")
    (tmp_path / "configs" / "model").mkdir(parents=True)
    (tmp_path / "configs" / "model" / "small.yaml").write_text("name: local-small\n")
    (tmp_path / "configs" / "config.yaml").write_text("defaults:\n  - _self_\n  - model: small\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return ComposeSpec(packaged=pkg_name, local="configs")


def test_packaged_operand_ignores_the_local_shadow(tmp_path, monkeypatch):
    spec = _packaged_with_local_shadow(tmp_path, monkeypatch, "cmp_pkg")
    result = compare_operands(
        [ConfigOperand(source="packaged"), ConfigOperand(source="default")],
        compose_spec=spec,
        cwd=tmp_path,
    )
    assert [(d.path, d.values) for d in result.differences] == [
        ("model.name", ("small", "local-small"))
    ]


def test_packaged_operand_with_config_name_selects_a_named_root(tmp_path, monkeypatch):
    spec = _packaged_with_local_shadow(tmp_path, monkeypatch, "cmp_named_pkg")
    result = compare_operands(
        [
            ConfigOperand(source="packaged", label="pkg"),
            ConfigOperand(source="packaged", config_name="configA", label="pkgA"),
            ConfigOperand(
                source="packaged", config_name="configA", overrides=("model=small",)
            ),
        ],
        compose_spec=spec,
        cwd=tmp_path,
    )
    (difference,) = result.differences
    # The override on a packaged root still resolves strictly packaged groups.
    assert difference.values == ("small", "large", "small")


def test_packaged_operand_refusals(tmp_path, monkeypatch):
    spec = _packaged_with_local_shadow(tmp_path, monkeypatch, "cmp_refuse_pkg")
    with pytest.raises(RefusalError, match="takes no reference"):
        compare_operands(
            [ConfigOperand(source="packaged", reference="x"), ConfigOperand()],
            compose_spec=spec,
        )
    with pytest.raises(RefusalError, match="declares none"):
        compare_operands(
            [ConfigOperand(source="packaged"), ConfigOperand()],
            compose_spec=ComposeSpec(packaged=None),
        )
    with pytest.raises(RefusalError, match="only to 'packaged'"):
        compare_operands(
            [ConfigOperand(source="default", config_name="configA"), ConfigOperand()],
            compose_spec=spec,
        )


def test_file_operand_inherits_the_project_search_path(tmp_path, monkeypatch):
    spec = _packaged_with_local_shadow(tmp_path, monkeypatch, "cmp_file_pkg")
    (tmp_path / "studies").mkdir()
    study = tmp_path / "studies" / "v1.yaml"
    study.write_text("defaults:\n  - _self_\n  - model: large\n")
    result = compare_operands(
        [ConfigOperand(source="file", reference=study), ConfigOperand(source="packaged")],
        compose_spec=spec,
        cwd=tmp_path,
    )
    assert result.differences[0].values == ("large", "small")
