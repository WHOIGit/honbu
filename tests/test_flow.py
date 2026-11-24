"""L2 flow: command declarations, the root contract, and the run manifest.

Same rule as the core tests — each of these pins a decision taken during
review, so a failure means a decision was reversed.
"""

from __future__ import annotations

import pytest
from pydantic import Field

from honbu import (
    ExperimentConfig,
    HashRegistry,
    OutputBlock,
    OutputLeaf,
    PortablePath,
    RefusalError,
    RuntimeConfig,
    SourceHash,
    StrictModel,
    validate_root_contract,
)
from honbu.errors import DeclarationError
from honbu.flow import CommandRegistry, CommandSpec, Manifest, StepSpec, TargetSpec

# --------------------------------------------------------------------------- #
# a project-shaped root
# --------------------------------------------------------------------------- #


class TrainingOutputs(OutputBlock):
    """A subclass whose only purpose is a different default — the case the old
    add-only contract rule would have forbidden."""

    dir_template: str | None = "{experiment.name}/{:timestamp}_{runtime.run_id}"
    metrics: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir="metrics"))
    figures: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir="figures"))


class DataBlock(StrictModel):
    uri: PortablePath
    split: str = "train"


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    data: DataBlock
    output_root: PortablePath = "./runs"
    training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)


# --------------------------------------------------------------------------- #
# the root contract
# --------------------------------------------------------------------------- #


def test_a_conforming_root_passes():
    validate_root_contract(Root)


def test_a_missing_base_block_fails():
    class NoRuntime(StrictModel):
        experiment: ExperimentConfig
        output_root: PortablePath = "./runs"
        training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)

    with pytest.raises(DeclarationError, match="missing the required 'runtime' block"):
        validate_root_contract(NoRuntime)


def test_a_block_of_the_wrong_type_fails():
    class Impostor(StrictModel):
        seed: int = 0

    class WrongRuntime(StrictModel):
        experiment: ExperimentConfig
        runtime: Impostor = Field(default_factory=Impostor)
        output_root: PortablePath = "./runs"
        training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)

    with pytest.raises(DeclarationError, match="must be RuntimeConfig or a subclass"):
        validate_root_contract(WrongRuntime)


def test_a_root_with_no_output_block_fails():
    class NoOutputs(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: PortablePath = "./runs"

    with pytest.raises(DeclarationError, match="declares no output block"):
        validate_root_contract(NoOutputs)


def test_unmarked_output_root_fails():
    from pathlib import Path

    class UnmarkedOutputRoot(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: Path = Path("./runs")
        training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)

    with pytest.raises(DeclarationError, match="output_root must carry PathMarker"):
        validate_root_contract(UnmarkedOutputRoot)


def test_the_guard_does_not_police_what_a_subclass_does_to_inherited_fields():
    """Deliberate. Policing this forbade a pattern one project already relies on
    and guarded a failure that announces itself the same day."""

    class OpinionatedRuntime(RuntimeConfig):
        run_id: str = "fixed"  # marker dropped, constraint-free, on purpose

    class Root2(StrictModel):
        experiment: ExperimentConfig
        runtime: OpinionatedRuntime = Field(default_factory=OpinionatedRuntime)
        output_root: PortablePath = "./runs"
        training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)

    validate_root_contract(Root2)


# --------------------------------------------------------------------------- #
# command names
# --------------------------------------------------------------------------- #


def test_a_bare_string_name_becomes_a_one_segment_tuple():
    assert CommandSpec(name="train").name == ("train",)


def test_a_two_segment_name_survives_an_internal_hyphen():
    """Flattening loses this: 'inspect-config-compare' cannot say which segment
    carried the hyphen."""
    spec = CommandSpec(name=("inspect", "config-compare"))
    assert spec.slug == "inspect-config-compare"
    assert spec.invocation == "inspect config-compare"
    assert spec.name == ("inspect", "config-compare")


def test_an_empty_name_is_refused():
    with pytest.raises(DeclarationError, match="at least one non-empty segment"):
        CommandSpec(name=())


# --------------------------------------------------------------------------- #
# the command registry
# --------------------------------------------------------------------------- #

TRAIN_TARGET = TargetSpec(
    template="training_outputs.dir_template",
    resolved="training_outputs.dir",
    root="output_root",
)


def test_declared_paths_are_validated_against_the_schema():
    bad = CommandSpec(
        name="train",
        target=TargetSpec(
            template="training_outputs.dir_templat",  # typo
            resolved="training_outputs.dir",
            root="output_root",
        ),
    )
    with pytest.raises(DeclarationError, match="dir_templat"):
        CommandRegistry([bad], root_type=Root)


def test_an_undeclared_hash_fails_at_construction():
    hashes = HashRegistry(
        [SourceHash(name="config_hash", build=lambda ctx: {"version": "1"})]
    )
    spec = CommandSpec(name="train", target=TRAIN_TARGET, hashes=("typo_hash",))
    with pytest.raises(DeclarationError, match="undeclared hashes"):
        CommandRegistry([spec], root_type=Root, hashes=hashes)


def test_duplicate_commands_are_refused():
    spec = CommandSpec(name="train", target=TRAIN_TARGET)
    with pytest.raises(DeclarationError, match="duplicate command"):
        CommandRegistry([spec, spec], root_type=Root)


def test_an_augmenting_command_needs_a_target_to_locate_the_run():
    spec = CommandSpec(name="eval", mode="augment")
    with pytest.raises(DeclarationError, match="declares no target"):
        CommandRegistry([spec], root_type=Root)


def test_a_read_only_command_declares_no_target():
    registry = CommandRegistry([CommandSpec(name=("inspect", "config"))], root_type=Root)
    assert registry.get("inspect-config").target is None


def test_a_deferred_hash_cannot_bind_a_from_hash_field():
    """Deferred hashes are run-time facts and are prevented structurally at import."""
    from typing import Annotated

    from honbu import FromHash, ResolvedString, validate_identity_fields

    class DataWithDeferred(StrictModel):
        uri: PortablePath
        dataset_hash: Annotated[ResolvedString, FromHash("dataset_hash")] = None

    class RootD(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        data: DataWithDeferred
        output_root: PortablePath = "./runs"
        training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)

    hashes = HashRegistry(
        [
            SourceHash(
                name="dataset_hash",
                build=lambda ctx: {"version": "1", "rows": 1},
                reads_inputs=True,
            )
        ]
    )
    with pytest.raises(DeclarationError, match="deferred past resolution"):
        validate_identity_fields(RootD, hashes)


def test_steps_are_declared_and_an_undeclared_name_is_refused():
    spec = CommandSpec(
        name="train",
        target=TRAIN_TARGET,
        steps=(
            StepSpec("prepare"),
            StepSpec("fit", scope=("training_outputs.metrics",)),
        ),
    )
    CommandRegistry([spec], root_type=Root)
    assert spec.step("fit").scope == ("training_outputs.metrics",)
    with pytest.raises(DeclarationError, match="declares no step 'typo'"):
        spec.step("typo")


def test_duplicate_steps_are_refused():
    spec = CommandSpec(
        name="train", target=TRAIN_TARGET, steps=(StepSpec("fit"), StepSpec("fit"))
    )
    with pytest.raises(DeclarationError, match="duplicate steps"):
        CommandRegistry([spec], root_type=Root)


# --------------------------------------------------------------------------- #
# the manifest
# --------------------------------------------------------------------------- #


def test_a_new_manifest_is_initialized_and_on_disk(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    assert manifest.state == "initialized"
    assert manifest.path.is_file()
    assert Manifest.load(tmp_path).state == "initialized"


def test_state_is_derived_from_the_last_reserved_event(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_lifecycle_event("started")
    assert manifest.state == "running"
    manifest.record_completion()
    assert manifest.state == "completed"


def test_a_project_event_does_not_move_the_state(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_lifecycle_event("started")
    manifest.record_event("dataset_scanned", rows=50_000)
    assert manifest.state == "running"
    assert manifest.data["timeline"][-1]["rows"] == 50_000


def test_a_project_cannot_write_a_reserved_event(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    with pytest.raises(RefusalError, match="reserved lifecycle event"):
        manifest.record_event("finished")


def test_interrupted_and_failed_are_distinguished_by_who_stopped_it(tmp_path):
    (tmp_path / "a").mkdir()
    signalled = Manifest.create(tmp_path / "a", command=("train",))
    signalled.record_failure(kind="KeyboardInterrupt", message="SIGINT", interrupted=True)
    assert signalled.state == "interrupted"

    (tmp_path / "b").mkdir()
    raised = Manifest.create(tmp_path / "b", command=("train",))
    raised.record_failure(kind="ValueError", message="bad shape", interrupted=False)
    assert raised.state == "failed"


def test_an_artifact_registered_before_an_interruption_survives(tmp_path):
    """The reason the manifest is live rather than written at completion."""
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_lifecycle_event("started")
    manifest.record_artifact("checkpoint", "checkpoints/last.pt")
    manifest.record_failure(kind="KeyboardInterrupt", message="SIGINT", interrupted=True)

    reloaded = Manifest.load(tmp_path)
    assert reloaded.data["artifacts"]["checkpoint"] == "checkpoints/last.pt"
    assert reloaded.state == "interrupted"


def test_registering_an_artifact_overwrites_rather_than_accumulates(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_artifact("checkpoint", "checkpoints/epoch-1.pt")
    manifest.record_artifact("checkpoint", "checkpoints/epoch-37.pt")
    assert manifest.data["artifacts"] == {"checkpoint": "checkpoints/epoch-37.pt"}


def test_best_is_stored_verbatim_and_nothing_is_interpreted(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_best(epoch=37, step=11_840, metrics={"map50": 0.613})
    best = manifest.data["best"]
    assert best == {"epoch": 37, "step": 11_840, "metrics": {"map50": 0.613}}


def test_completed_steps_are_recoverable_for_resume(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_lifecycle_event("started")
    manifest.record_event("step:prepare:started")
    manifest.record_event("step:prepare:completed")
    manifest.record_event("step:fit:started")
    manifest.record_failure(kind="KeyboardInterrupt", message="SIGINT", interrupted=True)
    assert manifest.completed_steps() == frozenset({"prepare"})


def test_invocations_accumulate_and_carry_a_config_index(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    first = manifest.record_invocation(
        command=("train",), argv="sati train", config_index=1, config_hash="sha256:aaa"
    )
    second = manifest.record_invocation(
        command=("eval",),
        argv="sati eval prediction.iou=0.5",
        config_index=2,
        config_hash="sha256:bbb",
    )
    assert (first, second) == (0, 1)
    assert [entry["config"] for entry in manifest.data["invocations"]] == [1, 2]


def test_timeline_events_reference_the_current_invocation(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_invocation(command=("train",), argv="sati train", config_index=1)
    manifest.record_lifecycle_event("started")
    assert manifest.data["timeline"][-1]["invocation"] == 0


def test_lineage_records_the_parent_without_interpreting_it(tmp_path):
    manifest = Manifest.create(tmp_path, command=("train",))
    manifest.record_parent(
        run_dir="runs/exp/aloof-marten",
        config_hash="sha256:aaa",
        artifact="checkpoints/last.ckpt",
    )
    assert manifest.data["parent"]["artifact"] == "checkpoints/last.ckpt"
