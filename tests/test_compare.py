"""Configuration comparison is levelled, N-way, and marker-aware."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import DERIVED, PORTABLE_PATH, FieldHash, HashRegistry, RefusalError, StrictModel
from honbu.compare import ABSENT, ConfigOperand, compare_configs, compare_operands, render_comparison
from honbu.flow import ComposeSpec


class Model(StrictModel):
    name: str = "small"


class Root(StrictModel):
    model: Model = Field(default_factory=Model)
    output_root: Annotated[Path, PORTABLE_PATH] = Path("./runs")
    run_id: Annotated[str | None, DERIVED] = None


FIRST = {"model": {"name": "small"}, "output_root": "./runs", "run_id": "a"}
SECOND = {"model": {"name": "large"}, "output_root": "./elsewhere", "run_id": "b"}


def test_literal_comparison_is_n_way_and_distinguishes_absence_from_null():
    result = compare_configs([{"value": None}, {}, {"value": 2}])
    assert result.differences[0].path == "value"
    assert result.differences[0].values == (None, ABSENT, 2)


def test_behavioral_projection_and_residuals_follow_markers():
    result = compare_configs([FIRST, SECOND], level="behavioral", root_type=Root)
    assert [difference.path for difference in result.differences] == ["model.name"]
    assert result.residual_paths == ("output_root", "run_id")


def test_by_hash_attributes_field_hashes_and_names_opaque_declarations():
    hashes = HashRegistry(
        [
            FieldHash("model_hash", include=["model"], version="1"),
        ],
        root_type=Root,
    )
    result = compare_configs([FIRST, SECOND], level="by_hash", root_type=Root, hashes=hashes)
    assert result.differences[0].moved_hashes == ("model_hash",)
    assert "moves model_hash" in render_comparison(result)


def test_unavailable_levels_refuse_instead_of_downgrading():
    with pytest.raises(RefusalError, match="root_type"):
        compare_configs([FIRST, SECOND], level="behavioral")
    with pytest.raises(RefusalError, match="hash registry"):
        compare_configs([FIRST, SECOND], level="by_hash", root_type=Root)


def test_operand_references_compose_defaults_and_apply_own_overrides(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("model:\n  name: small\n")
    result = compare_operands(
        [
            ConfigOperand(label="base"),
            ConfigOperand(overrides=("model.name=large",), label="override"),
        ],
        compose_spec=ComposeSpec(packaged=None),
        cwd=tmp_path,
    )
    assert result.differences[0].path == "model.name"
    assert result.labels == ("base", "override")


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
