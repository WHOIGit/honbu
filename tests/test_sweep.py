"""The sweep block: normalization, axis kinds, expansion, and the definition digest."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import (
    DIRECTIVE,
    ExperimentConfig,
    OutputBlock,
    PortablePath,
    RefusalError,
    ResolvedPath,
    RuntimeConfig,
    StrictModel,
)
from honbu.sweeps import (
    SweepBlock,
    SweepDeclaration,
    check_root_declares_sweep,
    axis_displacement_warnings,
    axis_kind,
    check_axis_paths,
    check_no_group_is_shadowed,
    check_no_override_contests_an_axis,
    check_no_path_claimed_twice,
    expand,
    normalize_axes,
    normalize_covariant_axes,
    override_key,
    plan_sweep,
    sweep_axes,
    sweep_block_of,
    sweep_definition_digest,
    without_sweep_block,
)


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
    sweep: SweepDeclaration = Field(default_factory=SweepBlock)


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


# --- well-formedness ---------------------------------------------------------


def test_a_covariant_axis_omitting_a_path_its_siblings_assign_is_refused():
    """A hole wearing the shape of an axis.

    The omitted path does not fail — it silently inherits the base, so one point of
    the diagonal is quietly not on it.
    """
    with pytest.raises(RefusalError, match="omits") as caught:
        normalize_covariant_axes(
            {"arch": [{"model.backbone": "a", "epochs": 1}, {"model.backbone": "b"}]}
        )
    assert "epochs" in str(caught.value)


def test_a_covariant_option_adding_a_path_its_siblings_lack_is_refused():
    with pytest.raises(RefusalError, match="adds"):
        normalize_covariant_axes(
            {"arch": [{"model.backbone": "a"}, {"model.backbone": "b", "epochs": 1}]}
        )


def test_a_single_option_covariant_axis_is_refused_as_a_constant():
    with pytest.raises(RefusalError, match="at least two") as caught:
        normalize_covariant_axes({"arch": [{"model.backbone": "a", "epochs": 1}]})
    assert "base configuration" in str(caught.value)


def test_an_empty_covariant_axis_is_refused_too():
    with pytest.raises(RefusalError, match="at least two"):
        normalize_covariant_axes({"arch": []})


def test_one_path_spelled_two_ways_is_caught_at_normalization():
    """A mapping cannot hold one key twice, so the flat and nested spellings are the
    only way to declare an ordinary axis's path twice — and they meet after
    normalization, not before."""
    with pytest.raises(RefusalError, match="declared twice"):
        normalize_axes({"model.backbone": ["a", "b"], "model": {"backbone": ["c"]}})


def test_two_axes_claiming_one_path_are_refused():
    axes = (
        *axes_of(block(axes={"epochs": [1, 2]})),
        *axes_of(block(axes={"epochs": [3, 4]})),
    )
    with pytest.raises(RefusalError, match="claimed by two axes"):
        check_no_path_claimed_twice(axes)


def test_an_axis_and_a_covariant_axis_claiming_one_path_are_refused():
    """The rule holds across kinds, which is the easier defect to write by accident."""
    axes = axes_of(
        block(
            axes={"epochs": [10, 20]},
            covariant_axes={
                "arch": [
                    {"model.backbone": "a", "epochs": 1},
                    {"model.backbone": "b", "epochs": 2},
                ]
            },
        )
    )
    with pytest.raises(RefusalError, match="claimed by two axes") as caught:
        check_no_path_claimed_twice(axes)
    assert "'epochs'" in str(caught.value)
    assert "arch" in str(caught.value)


# --- path validation ---------------------------------------------------------


def test_a_misspelled_leaf_fails_the_schema_check():
    axes = axes_of(block(axes={"model.backbon": ["a", "b"]}))
    with pytest.raises(RefusalError, match="fails the schema check"):
        check_axis_paths(axes, root_type=Root, composed=BASE)


def test_a_misspelled_container_key_fails_only_the_configuration_check():
    """The schema admits any key of a mapping, so this one gets past it.

    Left to a child it would fabricate a head named ``speces`` and surface as a
    per-child validation failure — the wrong error in the wrong place.
    """
    axes = axes_of(block(axes={"model.heads.speces.classes": [4, 8]}))
    with pytest.raises(RefusalError, match="fails the configuration check"):
        check_axis_paths(axes, root_type=Root, composed=BASE)


def test_a_path_descending_through_a_concrete_container_key_validates():
    axes = axes_of(block(axes={"model.heads.species.network.dropout": [0.1, 0.5]}))
    check_axis_paths(axes, root_type=Root, composed=BASE)


def test_a_bad_path_refuses_rather_than_preparing_zero_children():
    """Skipping is for a combination a validator rejects, not for a spoiled sweep."""
    bl = block(axes={"model.backbon": ["a", "b"]})
    with pytest.raises(RefusalError):
        plan_sweep(bl, root_type=Root, composed=BASE)


# --- overrides contesting an axis --------------------------------------------


def test_override_key_strips_every_prefix_form():
    assert override_key("epochs=10") == "epochs"
    assert override_key("+new.key=2") == "new.key"
    assert override_key("++forced=1") == "forced"
    assert override_key("~old.key") == "old.key"


def test_an_override_assigning_a_swept_path_is_refused_with_the_axis_form():
    axes = axes_of(block(axes={"epochs": [10, 20]}))
    with pytest.raises(RefusalError, match="already sweeps") as caught:
        check_no_override_contests_an_axis(axes, ["epochs=40"])
    assert "sweep.axes.epochs=[...]" in str(caught.value)


def test_a_contested_covariant_path_is_redirected_to_the_covariant_form():
    axes = axes_of(
        block(
            covariant_axes={
                "arch": [
                    {"model.backbone": "a", "epochs": 1},
                    {"model.backbone": "b", "epochs": 2},
                ]
            }
        )
    )
    with pytest.raises(RefusalError) as caught:
        check_no_override_contests_an_axis(axes, ["model.backbone=c"])
    assert "sweep.covariant_axes.arch=[...]" in str(caught.value)


def test_an_override_below_a_swept_group_is_refused_because_a_group_merges():
    """The substantive case: it survives every selection and leaks into all children."""
    axes = axes_of(block(axes={"data": ["nes-hf", "nes-raw"]}))
    with pytest.raises(RefusalError, match="already sweeps"):
        check_no_override_contests_an_axis(axes, ["data.split=val"])


def test_an_override_outside_the_swept_set_applies_to_every_child():
    axes = axes_of(block(axes={"epochs": [10, 20]}))
    check_no_override_contests_an_axis(axes, ["experiment.name=other", "output_root=/x"])


def test_an_override_redefining_the_sweep_is_not_refused():
    axes = axes_of(block(axes={"epochs": [10, 20]}))
    check_no_override_contests_an_axis(axes, ["sweep.axes.epochs=[1,2]", "sweep.gate=x"])


# --- the axis-wins warning ---------------------------------------------------


def test_an_axis_displacing_a_base_value_warns_and_names_what_it_displaced():
    axes = axes_of(block(axes={"epochs": [10, 20]}))
    (warning,) = axis_displacement_warnings(axes, BASE)
    assert "'epochs'" in warning and "10" in warning


def test_an_axis_the_base_does_not_set_warns_about_nothing():
    axes = axes_of(block(axes={"model.heads.species.network.dropout": [0.1, 0.5]}))
    assert axis_displacement_warnings(axes, BASE) == ()


def test_the_warning_is_emitted_once_per_axis_not_once_per_child():
    """A sweep of two hundred children must not bury it under two hundred copies."""
    bl = block(axes={"epochs": list(range(100)), "model.backbone": ["a", "b"]})
    plan = plan_sweep(bl, root_type=Root, composed=BASE)
    assert len(plan) == 200
    assert len(plan.warnings) == 2


# --- a group shadowed by the base configuration ------------------------------


def test_a_group_the_base_also_supplies_directly_is_refused():
    """A group is merged, not substituted, so the child gets the union.

    The check needs composition: after composing, the package path holds the merged
    product and carries no record of where each key came from.
    """
    axes = axes_of(block(axes={"data": ["nes-hf", "nes-raw"]}))

    def compose_without(group):
        assert group == "data"
        return {**BASE, "data": {"split": "train"}}

    with pytest.raises(RefusalError, match="merged rather than substituted") as caught:
        check_no_group_is_shadowed(axes, compose_without=compose_without)
    assert "split" in str(caught.value)


def test_a_group_the_base_leaves_alone_passes():
    axes = axes_of(block(axes={"data": ["nes-hf", "nes-raw"]}))
    check_no_group_is_shadowed(axes, compose_without=lambda group: {**BASE, "data": {}})


def test_the_shadow_check_is_skipped_when_no_compositor_is_supplied():
    """A sweep can be planned against a configuration dict alone."""
    plan = plan_sweep(block(axes={"data": ["nes-hf", "nes-raw"]}), root_type=Root, composed=BASE)
    assert len(plan) == 2


# --- the plan ----------------------------------------------------------------


def test_a_plan_carries_the_expansion_the_warnings_and_the_digest():
    bl = block(gate="runtime.config_id", axes={"epochs": [10, 20], "model.backbone": ["a", "b"]})
    plan = plan_sweep(bl, root_type=Root, composed=BASE, overrides=["experiment.name=x"])
    assert len(plan) == 4
    assert len(plan.axes) == 2
    assert plan.digest == sweep_definition_digest(BASE, root_type=Root, block=bl)


def test_a_plan_refuses_before_expanding_rather_than_expanding_something_wrong():
    bl = block(
        axes={"epochs": [10, 20]},
        covariant_axes={"arch": [{"epochs": 1}, {"epochs": 2}]},
    )
    with pytest.raises(RefusalError, match="claimed by two axes"):
        plan_sweep(bl, root_type=Root, composed=BASE)


# --- declaring the block on a root -------------------------------------------


def test_a_root_with_no_sweep_field_is_told_the_line_to_add():
    """Sweeping is not universal, so the block is not requirable. The cost of that
    is that a project which tries must be told the fix, not the rule it broke."""

    class Bare(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = Path("./runs")

    with pytest.raises(RefusalError, match="cannot sweep") as caught:
        check_root_declares_sweep(Bare)
    assert "sweep: SweepDeclaration" in str(caught.value)


def test_an_unmarked_sweep_field_is_refused_because_it_would_fail_silently():
    """Everything would work; only the digests would be wrong."""

    class Unmarked(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = Path("./runs")
        sweep: SweepBlock = Field(default_factory=SweepBlock)

    with pytest.raises(RefusalError, match="declared but unmarked") as caught:
        check_root_declares_sweep(Unmarked)
    assert "run by hand" in str(caught.value)


def test_a_properly_declared_root_passes():
    check_root_declares_sweep(Root)


def test_a_project_may_add_its_own_fields_to_the_block():
    """The block is the one place a sweep is declared, and where a project's sweeps
    go is a declaration of the same kind.

    Read through Honbu's own type instead, a project's added field would be refused
    as an extra input — so the block would be the single sweep definition only for
    Honbu's half of it. Honbu still reads its three fields and nothing else.
    """

    class ProjectSweep(SweepBlock):
        name: str = "default"
        dir: str = "./sweeps/{experiment.name}"

    class ProjectRoot(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = Path("./runs")
        sweep: Annotated[ProjectSweep, DIRECTIVE] = Field(default_factory=ProjectSweep)

    block = sweep_block_of(
        {
            "experiment": {"name": "reef"},
            "sweep": {"name": "lr", "dir": "./elsewhere", "axes": {"epochs": [1, 2]}},
        },
        root_type=ProjectRoot,
    )
    assert isinstance(block, SweepBlock)
    assert block.name == "lr"
    assert sweep_axes(block, root_type=Root)[0].label == "epochs"


def test_an_extended_block_is_still_strict():
    """What is relaxed is whose schema applies, not whether one does."""

    class ProjectSweep(SweepBlock):
        name: str = "default"

    class ProjectRoot(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = Path("./runs")
        sweep: Annotated[ProjectSweep, DIRECTIVE] = Field(default_factory=ProjectSweep)

    with pytest.raises(RefusalError, match="invalid 'sweep' block"):
        sweep_block_of(
            {"experiment": {"name": "reef"}, "sweep": {"naem": "lr", "axes": {"e": [1]}}},
            root_type=ProjectRoot,
        )


def test_the_check_fires_only_when_a_sweep_block_is_actually_present():
    """A project that never sweeps must not be asked to declare anything."""

    class Bare(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = Path("./runs")

    assert sweep_block_of({"experiment": {"name": "x"}}, root_type=Bare) is None


# --- the block does not reach a digest ---------------------------------------


def test_the_sweep_block_is_excluded_from_behavioral_sources():
    from honbu.core import excluded_source_paths

    assert ("sweep",) in excluded_source_paths(Root)


def test_a_child_digest_equals_the_same_configuration_run_by_hand():
    """The guarantee the marker exists for.

    Preparation strips the block from each child, so a hand-run configuration
    carries one where a child does not. If either digest read it the two would
    differ — and both runs would still succeed, with only the identities
    disagreeing.
    """
    from honbu import FieldHash, HashRegistry

    hashes = HashRegistry(
        [FieldHash(name="config_hash", include=["experiment", "epochs", "sweep"])]
    )
    authored = {**BASE, "sweep": {"gate": "runtime.config_id", "axes": {"epochs": [10, 20]}}}
    by_hand = Root.model_validate(authored)
    child = Root.model_validate(without_sweep_block(authored))

    assert (
        hashes.compute({"config": by_hand}).entries["config_hash"].value
        == hashes.compute({"config": child}).entries["config_hash"].value
    )


def test_the_exclusion_holds_even_when_a_hash_names_the_block_explicitly():
    """An include set cannot readmit a marked field. That is the whole point of
    applying marker exclusions after the include set rather than within it."""
    from honbu import FieldHash, HashRegistry

    hashes = HashRegistry([FieldHash(name="h", include=["experiment", "sweep"])])
    empty = Root.model_validate({**BASE, "sweep": {"axes": {}}})
    swept = Root.model_validate({**BASE, "sweep": {"axes": {"epochs": [1, 2, 3]}}})
    assert (
        hashes.compute({"config": empty}).entries["h"].value
        == hashes.compute({"config": swept}).entries["h"].value
    )


# --- 7.10 bug fixes and built-in compositor wiring ---------------------------


def test_empty_ordinary_axes_empty_nested_mappings_and_empty_covariant_options_refused():
    with pytest.raises(RefusalError, match="lists no values"):
        normalize_axes({"epochs": []})
    with pytest.raises(RefusalError, match="empty mapping"):
        normalize_axes({"model": {}})
    with pytest.raises(RefusalError, match="assigns no paths"):
        normalize_covariant_axes({"pair": [{}, {}]})
    # Single-value ordinary axes remain permitted for CLI pinning
    assert normalize_axes({"epochs": [10]}) == {"epochs": [10]}


def test_override_key_normalizes_slashes_and_package_suffixes():
    assert override_key("model/head@model.head=resnet") == "model.head"
    assert override_key("~model/head") == "model.head"
    assert override_key("++data/split=val") == "data.split"


def test_declared_block_type_unwraps_optional_and_annotated():
    from honbu.sweeps import declared_block_type

    class CustomSweep(SweepBlock):
        tag: str = "x"

    class OptSweepRoot(StrictModel):
        experiment: ExperimentConfig
        sweep: Annotated[CustomSweep | None, DIRECTIVE] = Field(default_factory=CustomSweep)

    assert declared_block_type(OptSweepRoot) is CustomSweep


def test_sequence_index_lookup_warns_when_axis_displaces_sequence_entry():
    class Item(StrictModel):
        weight: float = 1.0

    class SeqRoot(StrictModel):
        experiment: ExperimentConfig
        items: list[Item] = Field(default_factory=list)
        sweep: SweepDeclaration = Field(default_factory=SweepBlock)

    composed = {"experiment": {"name": "e"}, "items": [{"weight": 0.5}, {"weight": 1.5}]}
    plan = plan_sweep(
        SweepBlock(axes={"items.1.weight": [2.0, 3.0]}),
        root_type=SeqRoot,
        composed=composed,
    )
    assert any("items.1.weight=1.5" in w for w in plan.warnings)


def test_plan_sweep_built_in_compose_spec_handles_nested_group_shadowing(tmp_path):
    from honbu.flow import ComposeSpec

    class HeadCfg(StrictModel):
        layers: int = 2

    class ModelCfg(StrictModel):
        head: HeadCfg = Field(default_factory=HeadCfg)

    class NestedRoot(StrictModel):
        experiment: ExperimentConfig
        model: ModelCfg = Field(default_factory=ModelCfg)
        sweep: SweepDeclaration = Field(default_factory=SweepBlock)

    (tmp_path / "configs" / "model" / "head").mkdir(parents=True)
    (tmp_path / "configs" / "config.yaml").write_text(
        "defaults:\n  - _self_\n  - model/head: shallow\n"
        "experiment:\n  name: e\n"
        "model:\n  head:\n    layers: 99\n"  # shadows group!
    )
    (tmp_path / "configs" / "model" / "head" / "shallow.yaml").write_text("layers: 1\n")
    (tmp_path / "configs" / "model" / "head" / "deep.yaml").write_text("layers: 8\n")

    spec = ComposeSpec(packaged=None, local="configs")
    with pytest.raises(RefusalError, match="supplies 'model.head' directly"):
        plan_sweep(
            SweepBlock(axes={"model.head": ["shallow", "deep"]}),
            root_type=NestedRoot,
            composed={"experiment": {"name": "e"}, "model": {"head": {"layers": 99}}},
            compose_spec=spec,
            cwd=tmp_path,
        )
