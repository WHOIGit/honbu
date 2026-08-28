"""The sweep block: normalization, axis kinds, expansion, and the definition digest."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import (
    ExperimentConfig,
    OutputBlock,
    PortablePath,
    RefusalError,
    ResolvedPath,
    ResolvedString,
    RuntimeConfig,
    StrictModel,
)
from honbu.capabilities import (
    SweepBlock,
    axis_kind,
    expand,
    normalize_axes,
    sweep_axes,
    sweep_block_of,
    sweep_definition_digest,
    without_sweep_block,
)
from honbu.capabilities.sweep import normalize_covariant_axes


class Network(StrictModel):
    hidden_dims: list[int] = Field(default_factory=lambda: [128])
    dropout: float = 0.1


class Head(StrictModel):
    classes: int = 2
    network: Network = Field(default_factory=Network)


class Model(StrictModel):
    backbone: str = "resnet50"
    heads: dict[str, Head] = Field(default_factory=dict)


class Data(StrictModel):
    uri: PortablePath = Path("./data")
    split: str = "train"


class Outputs(OutputBlock):
    dir: ResolvedPath = None
    dir_template: str | None = "{experiment.name:slug}/{runtime.run_id}"


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    data: Data = Field(default_factory=Data)
    model: Model = Field(default_factory=Model)
    epochs: int = 10
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    sweep: SweepBlock = Field(default_factory=SweepBlock)


BASE = {
    "experiment": {"name": "reef"},
    "data": {"uri": "./data", "split": "train"},
    "model": {
        "backbone": "resnet50",
        "heads": {"species": {"classes": 12}, "damage": {"classes": 3}},
    },
    "epochs": 10,
    "output_root": "./runs",
}


def block(**kwargs) -> SweepBlock:
    return SweepBlock(**kwargs)


def axes_of(bl: SweepBlock) -> tuple:
    return sweep_axes(bl, root_type=Root)


# --- normalization -----------------------------------------------------------


def test_a_flat_dotted_key_and_the_nested_override_form_normalize_alike():
    """The two spellings of one axis have to land on the same path.

    An authored file writes ``model.backbone``; the compositor reads the equivalent
    override as a two-level mapping. If these diverged, an axis would validate under
    one spelling and fabricate a path under the other.
    """
    flat = normalize_axes({"model.backbone": ["a", "b"]})
    nested = normalize_axes({"model": {"backbone": ["a", "b"]}})
    assert flat == nested == {"model.backbone": ["a", "b"]}


def test_normalization_descends_to_a_list_and_stops_there():
    axes = normalize_axes(
        {"epochs": [1, 2], "model": {"heads": {"species": {"classes": [4, 8]}}}}
    )
    assert axes == {"epochs": [1, 2], "model.heads.species.classes": [4, 8]}


def test_a_scalar_axis_value_is_refused_as_a_constant():
    with pytest.raises(RefusalError, match="must list its values"):
        normalize_axes({"epochs": 10})


def test_a_string_axis_value_is_not_read_as_a_sequence_of_characters():
    with pytest.raises(RefusalError, match="must list its values"):
        normalize_axes({"model.backbone": "resnet50"})


def test_covariant_options_flatten_their_assigned_paths():
    out = normalize_covariant_axes(
        {"arch": [{"model": {"backbone": "a"}, "epochs": 1}, {"model.backbone": "b", "epochs": 2}]}
    )
    assert out == {
        "arch": [{"model.backbone": "a", "epochs": 1}, {"model.backbone": "b", "epochs": 2}]
    }


def test_a_covariant_option_that_is_not_a_mapping_is_refused():
    with pytest.raises(RefusalError, match="must be a mapping"):
        normalize_covariant_axes({"arch": ["resnet50", "convnext"]})


# --- axis kinds --------------------------------------------------------------


def test_an_axis_over_a_model_typed_field_listing_names_is_a_group_axis():
    """``data=nes-hf`` selects a whole file; the schema is what says so.

    Assigning the string to a block-typed field could not validate, so group
    selection is the only available reading.
    """
    assert axis_kind(Root, "data", ["nes-hf", "nes-raw"]) == "group"


def test_an_axis_over_a_scalar_is_a_path_axis():
    assert axis_kind(Root, "model.backbone", ["resnet50", "convnext"]) == "path"
    assert axis_kind(Root, "epochs", [10, 20]) == "path"


def test_an_axis_assigning_whole_subtrees_stays_a_path_axis():
    """Mappings are values, not group names, even over a block-typed field."""
    assert axis_kind(Root, "data", [{"split": "train"}, {"split": "val"}]) == "path"


def test_a_path_below_a_configuration_keyed_container_is_a_path_axis():
    """The reach a group axis structurally cannot have.

    A group file's package path is static, so no group could target
    ``model.heads.species`` without hardcoding a key the configuration chose.
    """
    kind = axis_kind(Root, "model.heads.species.network.dropout", [0.1, 0.5])
    assert kind == "path"


# --- expansion ---------------------------------------------------------------


def test_two_axes_expand_to_the_product():
    axes = axes_of(block(axes={"epochs": [10, 20], "model.backbone": ["a", "b", "c"]}))
    assert [len(axis) for axis in axes] == [2, 3]
    assert len(expand(axes)) == 6


def test_every_child_carries_its_realized_values_and_its_assignments():
    axes = axes_of(block(axes={"epochs": [10, 20]}))
    children = expand(axes)
    assert [child.values for child in children] == [{"epochs": 10}, {"epochs": 20}]
    assert [child.assignments for child in children] == [{"epochs": 10}, {"epochs": 20}]
    assert [child.index for child in children] == [0, 1]


def test_a_group_axis_produces_a_selection_rather_than_an_assignment():
    """A selection has to reach the compositor; an assignment does not."""
    (axis,) = axes_of(block(axes={"data": ["nes-hf", "nes-raw"]}))
    assert axis.kind == "group"
    child = expand([axis])[0]
    assert child.selections == {"data": "nes-hf"}
    assert child.assignments == {}


def test_a_covariant_axis_contributes_one_dimension_not_one_per_path():
    """The whole reason covariance is a distinct construct.

    Two paths moving together is a diagonal of length two, not a two-by-two grid.
    """
    axes = axes_of(
        block(
            covariant_axes={
                "arch": [
                    {"model.backbone": "resnet50", "epochs": 10},
                    {"model.backbone": "convnext", "epochs": 40},
                ]
            }
        )
    )
    (axis,) = axes
    assert axis.kind == "covariant"
    assert axis.paths == ("model.backbone", "epochs")
    children = expand(axes)
    assert len(children) == 2
    assert children[0].assignments == {"model.backbone": "resnet50", "epochs": 10}
    assert children[1].assignments == {"model.backbone": "convnext", "epochs": 40}


def test_a_covariant_axis_composes_with_an_ordinary_one():
    axes = axes_of(
        block(
            axes={"model.heads.species.network.dropout": [0.1, 0.3, 0.5]},
            covariant_axes={
                "arch": [
                    {"model.backbone": "resnet50", "epochs": 10},
                    {"model.backbone": "convnext", "epochs": 40},
                ]
            },
        )
    )
    children = expand(axes)
    assert len(children) == 6
    # Every child's covarying paths come from one option, never a mix.
    pairs = {(c.assignments["model.backbone"], c.assignments["epochs"]) for c in children}
    assert pairs == {("resnet50", 10), ("convnext", 40)}


def test_expansion_of_no_axes_is_empty():
    assert expand(()) == ()


# --- reading the block off a configuration -----------------------------------


def test_an_absent_or_empty_block_is_not_a_sweep():
    """Projects default the field to an empty block so an override can address it.

    So "the key is present" cannot be the test for "a sweep was asked for".
    """
    assert sweep_block_of(BASE) is None
    assert sweep_block_of({**BASE, "sweep": {"gate": None, "axes": {}}}) is None


def test_a_block_with_axes_is_a_sweep():
    bl = sweep_block_of({**BASE, "sweep": {"gate": "runtime.config_id", "axes": {"epochs": [1, 2]}}})
    assert bl is not None and bl.gate == "runtime.config_id"


def test_an_unknown_key_in_the_block_is_refused():
    with pytest.raises(RefusalError, match="invalid 'sweep' block"):
        sweep_block_of({**BASE, "sweep": {"axes": {"epochs": [1]}, "strategy": "grid"}})


def test_a_non_mapping_block_is_refused():
    with pytest.raises(RefusalError, match="must be a mapping"):
        sweep_block_of({**BASE, "sweep": ["epochs"]})


# --- the definition digest ---------------------------------------------------


def test_stripping_the_block_leaves_the_rest_untouched():
    stripped = without_sweep_block({**BASE, "sweep": {"axes": {"epochs": [1, 2]}}})
    assert stripped == BASE
    assert "sweep" not in stripped


def test_two_sweeps_differing_only_in_output_location_share_a_definition_digest():
    """The digest is a claim about the experiment, not about the filesystem."""
    bl = block(axes={"epochs": [10, 20]})
    here = sweep_definition_digest(BASE, root_type=Root, block=bl)
    there = sweep_definition_digest(
        {**BASE, "output_root": "/scratch/elsewhere"}, root_type=Root, block=bl
    )
    assert here == there


def test_an_axis_gaining_a_value_changes_the_definition_digest():
    two = sweep_definition_digest(BASE, root_type=Root, block=block(axes={"epochs": [10, 20]}))
    three = sweep_definition_digest(
        BASE, root_type=Root, block=block(axes={"epochs": [10, 20, 30]})
    )
    assert two != three


def test_the_gate_is_not_part_of_the_definition_digest():
    """The gate says how children are told apart, not what they are."""
    axes = {"epochs": [10, 20]}
    unnamed = sweep_definition_digest(BASE, root_type=Root, block=block(axes=axes))
    named = sweep_definition_digest(
        BASE, root_type=Root, block=block(gate="runtime.config_id", axes=axes)
    )
    assert unnamed == named


def test_the_flat_and_nested_spellings_of_one_sweep_share_a_definition_digest():
    flat = sweep_definition_digest(
        BASE, root_type=Root, block=block(axes={"model.backbone": ["a", "b"]})
    )
    nested = sweep_definition_digest(
        BASE, root_type=Root, block=block(axes={"model": {"backbone": ["a", "b"]}})
    )
    assert flat == nested
