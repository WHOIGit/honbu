"""Preparation: every child written in the initialized state, and nothing run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import (
    ExperimentConfig,
    FieldHash,
    FromHash,
    HashRegistry,
    OutputBlock,
    PortablePath,
    RefusalError,
    ResolvedPath,
    ResolvedString,
    RuntimeConfig,
    StrictModel,
    TemplateContext,
    TemplatedIdentity,
)
from honbu.sweeps import (
    CHILD_COORDINATE_NAME,
    SWEEP_INDEX_NAME,
    SweepBlock,
    SweepDeclaration,
    assign_path,
    child_configuration,
    prepare_sweep,
    read_sweep_index,
    sweep_identity,
)
from honbu.sweeps import Coordinate
from honbu.flow import (
    CONFIG_DIRNAME,
    CommandSpec,
    ComposeSpec,
    Manifest,
    TargetSpec,
    compose_config,
    refuse_active_sweep_block,
    sweep_is_active,
)

from datetime import datetime, timezone

CONTEXT = TemplateContext(
    now_local=datetime(2026, 8, 28, 9, 0, 0),
    now_utc=datetime(2026, 8, 28, 13, 0, 0, tzinfo=timezone.utc),
    seed="fixedseed",
)


class Runtime(RuntimeConfig):
    config_hash: Annotated[ResolvedString, FromHash("config_hash")] = None
    config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"


class Outputs(OutputBlock):
    dir: ResolvedPath = None
    dir_template: str | None = "{runtime.sweep_id}/{runtime.config_id}"


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: Runtime = Field(default_factory=Runtime)
    epochs: int = 10
    lr: float = 0.1
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    sweep: SweepDeclaration = Field(default_factory=SweepBlock)


TARGET = TargetSpec(
    template="outputs.dir_template", resolved="outputs.dir", root="output_root"
)
TRAIN = CommandSpec(name=("train",), target=TARGET, hashes=("config_hash",))
HASHES = HashRegistry([FieldHash(name="config_hash", include=["experiment", "epochs", "lr"])])


def base(tmp_path, **extra):
    return {
        "experiment": {"name": "reef"},
        "epochs": 10,
        "lr": 0.1,
        "output_root": str(tmp_path / "runs"),
        "sweep": {"gate": "runtime.config_id", "axes": {}, "covariant_axes": {}},
        **extra,
    }


def prepare(composed, tmp_path, **kwargs):
    kwargs.setdefault("sweep_dir", tmp_path / "sweeps" / "one")
    return prepare_sweep(
        composed,
        root_type=Root,
        command=TRAIN,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
        argv_text="sweep prepare",
        **kwargs,
    )


def swept(tmp_path, axes, **extra):
    composed = base(tmp_path)
    composed["sweep"] = {"gate": "runtime.config_id", "axes": axes, **extra}
    return composed


# --- what preparation writes -------------------------------------------------


def test_every_child_is_written_in_the_initialized_state(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20], "lr": [0.1, 0.2]}), tmp_path)
    assert len(result) == 4
    for child in result.children:
        assert child.created
        assert Manifest.load(child.run_dir).state == "initialized"


def test_every_child_holds_its_configuration_bundle(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    for child in result.children:
        config_dir = child.run_dir / CONFIG_DIRNAME
        names = {path.name for path in config_dir.iterdir()}
        assert "composed.yaml" in names
        assert "resolved.yaml" in names


def test_preparation_runs_nothing(tmp_path):
    """No step is recorded, and no work output exists."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    for child in result.children:
        manifest = Manifest.load(child.run_dir)
        assert manifest.completed_steps() == frozenset()
        assert manifest.data["best"] is None


def test_children_land_in_distinct_directories(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20], "lr": [0.1, 0.2]}), tmp_path)
    assert len({child.run_dir for child in result.children}) == 4


def test_the_child_records_the_axis_values_that_produced_it(tmp_path):
    """In the configuration directory: it is the desugared form of the axes,
    legible without re-deriving the matrix."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    child = result.children[0]
    record = json.loads((child.run_dir / CONFIG_DIRNAME / CHILD_COORDINATE_NAME).read_text())
    assert record["values"] == {"epochs": 10}
    assert record["index"] == 0
    assert record["gate"] == "runtime.config_id"


def test_the_child_manifest_records_its_membership(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    manifest = Manifest.load(result.children[1].run_dir)
    events = [e for e in manifest.data["timeline"] if e["event"] == "sweep:member"]
    assert len(events) == 1
    assert events[0]["sweep_id"] == result.sweep_id
    assert events[0]["values"] == {"epochs": 20}


def test_the_sweep_identity_reaches_the_child_manifest(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    manifest = Manifest.load(result.children[0].run_dir)
    assert manifest.data["identities"]["sweep_id"] == result.sweep_id


def test_children_live_under_the_sweep_when_the_template_names_it(tmp_path):
    """Which is what feeding sweep_id to the resolver buys."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    for child in result.children:
        assert child.run_dir.parent.name == result.sweep_id


# --- a child is an ordinary run ----------------------------------------------


def test_a_child_carries_no_sweep_block(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    composed = (result.children[0].run_dir / CONFIG_DIRNAME / "composed.yaml").read_text()
    assert "sweep:" not in composed


def test_a_child_digest_equals_the_same_configuration_run_by_hand(tmp_path):
    """The guarantee. Both the stripping and the marker are doing work here."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    child = Manifest.load(result.children[0].run_dir)

    by_hand = Root.model_validate({**base(tmp_path), "epochs": 10})
    expected = HASHES.compute({"config": by_hand}).entries["config_hash"].value
    assert child.data["hashes"]["config_hash"]["value"] == expected


# --- idempotence -------------------------------------------------------------


def test_re_preparing_leaves_existing_children_untouched(tmp_path):
    first = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    stamps = {c.run_dir: (c.run_dir / "manifest.json").stat().st_mtime_ns for c in first.children}

    second = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    assert [c.created for c in second.children] == [False, False]
    for child in second.children:
        assert (child.run_dir / "manifest.json").stat().st_mtime_ns == stamps[child.run_dir]


def test_growing_a_sweep_prepares_only_the_child_it_gained(tmp_path):
    """The case re-preparation exists for: add a value, run the one child you got.

    A sweep is added to, the way an evaluation is added to a run. This holds even
    though the child template names the sweep, because the sweep's identity comes
    from its directory rather than from its definition.
    """
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    second = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    assert len(second) == 3
    assert sum(child.created for child in second.children) == 1


def test_growing_a_sweep_leaves_existing_children_where_they_are(tmp_path):
    """What the directory-anchored identity buys, stated as the filesystem fact.

    Under a definition-derived identity these directories all moved, orphaning
    whatever had already run — which is precisely the situation re-preparation is
    supposed to handle.
    """
    first = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    second = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    assert {c.run_dir for c in first.children} < {c.run_dir for c in second.children}


def test_growing_a_sweep_keeps_its_identity_and_changes_its_hash(tmp_path):
    """The two the glossary distinguishes, and which an earlier version collapsed.

    ``sweep_id`` says which sweep this is; ``sweep_hash`` says what its definition
    currently is. Growing an axis changes the second and must not touch the first.
    """
    first = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    second = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    assert first.sweep_id == second.sweep_id
    assert first.sweep_hash != second.sweep_hash


def test_two_sweep_directories_are_two_sweeps(tmp_path):
    """The identity is the directory, so one definition prepared twice in two places
    is two sweeps rather than one confused one."""
    here = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path,
                   sweep_dir=tmp_path / "sweeps" / "here")
    there = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path,
                    sweep_dir=tmp_path / "sweeps" / "there")
    assert here.sweep_id != there.sweep_id
    assert here.sweep_hash == there.sweep_hash


def test_a_sweep_keeps_its_identity_if_its_directory_moves(tmp_path):
    """Derived from the path on first write, then persisted — so a move does not
    silently rename the sweep."""
    original = tmp_path / "sweeps" / "one"
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path, sweep_dir=original)

    moved = tmp_path / "sweeps" / "moved"
    original.rename(moved)
    again = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path, sweep_dir=moved)
    assert again.sweep_id == result.sweep_id
    assert again.sweep_id != sweep_identity(moved)


# --- shrinking: nothing is deleted -------------------------------------------


def test_removing_an_axis_value_retires_its_child_rather_than_dropping_it(tmp_path):
    """A completed child holds results. An index that stopped mentioning it would
    leave the directory on disk and unreferenced — the silent omission the index
    exists to prevent."""
    first = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    dropped = first.children[2]

    second = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    assert len(second) == 2
    assert len(second.retired) == 1
    assert second.retired[0]["run_dir"] == str(dropped.run_dir)
    assert second.retired[0]["in_definition"] is False


def test_a_retired_child_stays_in_the_index_and_on_disk(tmp_path):
    first = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    dropped = first.children[2].run_dir
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)

    assert (dropped / "manifest.json").is_file()
    index = read_sweep_index(tmp_path / "sweeps" / "one")
    entries = {e["run_dir"]: e for e in index["children"]}
    assert entries[str(dropped)]["in_definition"] is False
    assert sum(e["in_definition"] for e in index["children"]) == 2


def test_restoring_a_removed_value_un_retires_its_child(tmp_path):
    first = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    third = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)

    assert third.retired == ()
    assert len(third) == 3
    # And the child that came back is the one that was there, not a new one.
    assert {c.run_dir for c in third.children} == {c.run_dir for c in first.children}
    assert not any(c.created for c in third.children)


def test_a_completed_child_survives_re_preparation(tmp_path):
    first = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    manifest = Manifest.load(first.children[0].run_dir)
    manifest.record_lifecycle_event("started")
    manifest.record_outcome()
    assert manifest.state == "completed"

    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    assert Manifest.load(first.children[0].run_dir).state == "completed"


def test_the_sweep_identity_is_stable_across_preparations(tmp_path):
    """A fresh identity would make re-preparation produce a second sweep pointing at
    the same children, and the second one would look empty."""
    first = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    second = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    assert first.sweep_id == second.sweep_id


# --- nothing is written until every check passes ------------------------------


def test_a_gate_collision_leaves_no_child_behind(tmp_path):
    """A collision found after eight children exist is eight directories to reason
    about. So the check runs over the whole expansion, before anything lands."""
    composed = swept(tmp_path, {"epochs": [10, 20]})
    composed["sweep"]["gate"] = "experiment.name"
    composed["outputs"] = {"dir_template": "{runtime.sweep_id}/{experiment.name:slug}"}

    with pytest.raises(RefusalError, match="does not distinguish"):
        prepare(composed, tmp_path)
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").rglob("manifest.json"))


def test_an_unstable_gate_is_refused_before_anything_is_written(tmp_path):
    composed = swept(tmp_path, {"epochs": [10, 20]})
    composed["sweep"]["gate"] = "runtime.run_id"
    composed["outputs"] = {"dir_template": "{runtime.sweep_id}/{runtime.run_id}"}

    with pytest.raises(RefusalError, match="not stable"):
        prepare(composed, tmp_path)
    assert not list(tmp_path.rglob("manifest.json"))


def test_a_bad_axis_path_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(RefusalError, match="fails the schema check"):
        prepare(swept(tmp_path, {"epcohs": [10, 20]}), tmp_path)
    assert not list(tmp_path.rglob("manifest.json"))


def test_a_sweep_with_no_gate_is_refused(tmp_path):
    composed = swept(tmp_path, {"epochs": [10, 20]})
    composed["sweep"]["gate"] = None
    with pytest.raises(RefusalError, match="declares no gate"):
        prepare(composed, tmp_path)


def test_preparing_an_inactive_block_is_refused(tmp_path):
    with pytest.raises(RefusalError, match="no active 'sweep' block"):
        prepare(base(tmp_path), tmp_path)


# --- the sweep index ---------------------------------------------------------


def test_the_index_records_every_child_and_where_it_went(tmp_path):
    sweep_dir = tmp_path / "sweeps" / "one"
    result = prepare(
        swept(tmp_path, {"epochs": [10, 20]}), tmp_path, sweep_dir=sweep_dir
    )
    index = read_sweep_index(sweep_dir)
    assert index["sweep_id"] == result.sweep_id
    assert index["gate"] == "runtime.config_id"
    assert len(index["children"]) == 2
    for entry, child in zip(index["children"], result.children):
        assert entry["run_dir"] == str(child.run_dir)
        assert entry["values"] == dict(child.coordinate.values)


def test_the_index_lands_in_the_sweep_configuration_directory(tmp_path):
    sweep_dir = tmp_path / "sweeps" / "one"
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path, sweep_dir=sweep_dir)
    assert (sweep_dir / CONFIG_DIRNAME / SWEEP_INDEX_NAME).is_file()


def test_a_missing_index_is_a_refusal_naming_the_path(tmp_path):
    with pytest.raises(RefusalError, match="no sweep index"):
        read_sweep_index(tmp_path / "nowhere")


def test_the_index_records_the_current_definition_digest(tmp_path):
    """It is allowed to change; the identity beside it is not."""
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    grown = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    index = read_sweep_index(tmp_path / "sweeps" / "one")
    assert index["sweep_hash"] == grown.sweep_hash
    assert index["sweep_id"] == grown.sweep_id


# --- group axes need the compositor ------------------------------------------


def test_a_group_axis_without_a_compositor_is_refused(tmp_path):
    """A group selection carries a whole file and cannot be applied to a dict."""

    class Data(StrictModel):
        uri: str = "a"

    class WithData(Root):
        data: Data = Field(default_factory=Data)

    composed = swept(tmp_path, {"data": ["alpha", "beta"]})
    composed["data"] = {"uri": "a"}
    with pytest.raises(RefusalError, match="must be applied during composition"):
        prepare_sweep(
            composed,
            root_type=WithData,
            command=TRAIN,
            hashes=HASHES,
            context=CONTEXT,
            cwd=tmp_path,
            sweep_dir=tmp_path / "sweeps" / "blind",
        )


def test_a_group_axis_composes_each_child_through_the_compositor(tmp_path):
    """End to end, with real configuration files."""
    configs = tmp_path / "configs"
    (configs / "data").mkdir(parents=True)
    (configs / "config.yaml").write_text(
        "defaults:\n  - data: alpha\n  - _self_\n"
        "experiment:\n  name: reef\n"
        "epochs: 10\n"
        f"output_root: {tmp_path / 'runs'}\n"
        "outputs:\n  dir_template: '{runtime.sweep_id}/{runtime.config_id}'\n"
        "sweep:\n  gate: runtime.config_id\n  axes: {}\n  covariant_axes: {}\n"
    )
    (configs / "data" / "alpha.yaml").write_text("uri: /alpha\n")
    (configs / "data" / "beta.yaml").write_text("uri: /beta\n")

    class Data(StrictModel):
        uri: str

    class WithData(Root):
        data: Data

    # The identity must read what the sweep varies, or every child is the same
    # configuration and the gate says so. See the test below.
    hashes = HashRegistry([FieldHash(name="config_hash", include=["experiment", "epochs", "data"])])
    spec = ComposeSpec(packaged=None, local="configs")

    def compose_child(selections):
        tokens = [f"{group}={option}" for group, option in selections.items()]
        return compose_config(spec, overrides=tokens, cwd=tmp_path)

    composed = compose_config(
        spec, overrides=["sweep.axes.data=[alpha,beta]"], cwd=tmp_path
    )
    result = prepare_sweep(
        composed,
        root_type=WithData,
        command=TRAIN,
        hashes=hashes,
        context=CONTEXT,
        cwd=tmp_path,
        sweep_dir=tmp_path / "sweeps" / "groups",
        compose_child=compose_child,
    )
    assert len(result) == 2
    uris = set()
    for child in result.children:
        text = (child.run_dir / CONFIG_DIRNAME / "resolved.yaml").read_text()
        uris.add("/alpha" if "/alpha" in text else "/beta")
    assert uris == {"/alpha", "/beta"}


def test_a_gate_blind_to_what_the_sweep_varies_is_caught(tmp_path):
    """Sweeping something the configuration identity does not read produces
    children that are, by that identity, the same run. The gate says so — which is
    a real diagnosis and not an artifact of the check.
    """
    composed = swept(tmp_path, {"epochs": [10, 20]})
    blind = HashRegistry([FieldHash(name="config_hash", include=["experiment"])])
    with pytest.raises(RefusalError, match="varies by \\['epochs'\\]"):
        prepare_sweep(
            composed,
            root_type=Root,
            command=TRAIN,
            hashes=blind,
            context=CONTEXT,
            cwd=tmp_path,
            sweep_dir=tmp_path / "sweeps" / "blind",
        )


# --- applying an axis to a composed tree -------------------------------------


def test_assign_path_sets_a_nested_leaf():
    node = {"model": {"backbone": "a"}}
    assign_path(node, "model.backbone", "b")
    assert node == {"model": {"backbone": "b"}}


def test_assign_path_creates_a_leaf_absent_because_it_was_schema_defaulted():
    node = {"model": {}}
    assign_path(node, "model.dropout", 0.5)
    assert node == {"model": {"dropout": 0.5}}


def test_child_configuration_drops_the_block_and_applies_the_axes():
    coordinate = Coordinate(
        index=0, values={"epochs": 20}, assignments={"epochs": 20}, selections={}
    )
    config = child_configuration({"epochs": 10, "sweep": {"axes": {}}}, coordinate)
    assert config == {"epochs": 20}


def test_the_base_is_not_mutated_by_building_a_child():
    original = {"model": {"heads": {"a": {"n": 1}}}, "sweep": {"axes": {}}}
    coordinate = Coordinate(
        index=0,
        values={"model.heads.a.n": 9},
        assignments={"model.heads.a.n": 9},
        selections={},
    )
    child_configuration(original, coordinate)
    assert original["model"]["heads"]["a"]["n"] == 1


# --- a producing command refuses a sweep -------------------------------------


def test_an_active_block_is_recognized_without_the_schema():
    assert sweep_is_active({"sweep": {"axes": {"epochs": [1, 2]}}})
    assert sweep_is_active({"sweep": {"covariant_axes": {"arch": [{}, {}]}}})


def test_a_gate_alone_is_inert():
    assert not sweep_is_active({"sweep": {"gate": "runtime.config_id", "axes": {}}})
    assert not sweep_is_active({"sweep": {}})
    assert not sweep_is_active({})


def test_a_producing_command_refuses_an_active_block():
    with pytest.raises(RefusalError, match="describes more than one run") as caught:
        refuse_active_sweep_block(
            {"sweep": {"axes": {"epochs": [1, 2]}}}, invocation="train"
        )
    assert "Prepare the sweep instead" in str(caught.value)


def test_a_producing_command_runs_normally_with_an_inert_block():
    refuse_active_sweep_block({"sweep": {"gate": "runtime.config_id"}}, invocation="train")
