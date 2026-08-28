"""The run: the bundle in three waves, steps, resume, and closing out the manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from pydantic import Field

from honbu import (
    ExperimentConfig,
    FieldHash,
    FromHash,
    HashRegistry,
    MANIFEST_NAME,
    OutputBlock,
    OutputLeaf,
    PortablePath,
    RefusalError,
    ResolvedPath,
    ResolvedString,
    RuntimeConfig,
    SourceHash,
    StrictModel,
    TemplateContext,
)
from honbu.errors import DeclarationError
from honbu.flow import (
    CommandSpec,
    Manifest,
    initialize_run,
    StepSpec,
    TargetSpec,
    config_dir_for,
    open_run,
    resolve,
)

CONTEXT = TemplateContext(
    now_local=datetime(2026, 8, 26, 14, 30, 0),
    now_utc=datetime(2026, 8, 26, 18, 30, 0, tzinfo=timezone.utc),
    seed="fixedseed",
)


class Runtime(RuntimeConfig):
    config_hash: Annotated[ResolvedString, FromHash("config_hash")] = None


class Prepared(StrictModel):
    dataset_hash: Annotated[ResolvedString, FromHash("dataset_hash")] = None


class Data(StrictModel):
    uri: PortablePath
    prepared: Prepared = Field(default_factory=Prepared)


class TrainingOutputs(OutputBlock):
    dir: ResolvedPath = None
    dir_template: str | None = "{experiment.name:slug}/run"
    checkpoints: OutputLeaf = Field(
        default_factory=lambda: OutputLeaf(dir=Path("checkpoints"))
    )
    figures: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir=Path("figures")))


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: Runtime = Field(default_factory=Runtime)
    data: Data
    epochs: int = 10
    output_root: PortablePath = Path("./runs")
    training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)


TARGET = TargetSpec(
    template="training_outputs.dir_template",
    resolved="training_outputs.dir",
    root="output_root",
)

TRAIN = CommandSpec(
    name=("train",),
    target=TARGET,
    hashes=("config_hash", "dataset_hash"),
    steps=(
        StepSpec("prepare", on_resume="restart"),
        StepSpec("fit", scope=("training_outputs.checkpoints",)),
        StepSpec("evaluate", scope=("training_outputs.figures",), on_resume="restart"),
    ),
    # `evaluate` is optional: a trained run is complete without it (D25).
    completion=("prepare", "fit"),
)

EVAL = CommandSpec(
    name=("eval",),
    mode="augment",
    target=TARGET,
    config_sources=("resolved", "continue"),
    scope=("training_outputs.figures",),
    hashes=("config_hash",),
)

HASHES = HashRegistry(
    [
        FieldHash(name="config_hash", include=["experiment", "data", "epochs"]),
        SourceHash(
            name="dataset_hash",
            build=lambda ctx: {"version": "1", "rows": ctx.get("rows", 0)},
            reads_inputs=True,
        ),
    ],
    root_type=Root,
)

COMPOSED = {"experiment": {"name": "reef v3"}, "data": {"uri": "./datasets/coco8"}}


def _resolution(tmp_path, composed=None, *, command=TRAIN, **kwargs):
    return resolve(
        composed or COMPOSED,
        root_type=Root,
        command=command,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
        **kwargs,
    )


def _open(tmp_path, composed=None, *, command=TRAIN, argv="train", **kwargs):
    resolution = _resolution(
        tmp_path,
        composed,
        command=command,
        **{k: v for k, v in kwargs.items() if k == "existing_target"},
    )
    return open_run(
        resolution,
        composed=composed or COMPOSED,
        hashes=HASHES,
        argv_text=argv,
        **{k: v for k, v in kwargs.items() if k != "existing_target"},
    )


# --------------------------------------------------------------------------- #
# the bundle, wave one
# --------------------------------------------------------------------------- #


def test_the_bundle_is_on_disk_before_work_starts(tmp_path):
    with _open(tmp_path) as run:
        config = run.config_dir
        assert (config / "composed.yaml").is_file()
        assert (config / "resolved.yaml").is_file()
        assert (config / "hashes" / "config_hash.json").is_file()
        assert run.manifest.state == "running"


def test_the_first_configuration_lives_at_the_unsuffixed_path(tmp_path):
    with _open(tmp_path) as run:
        assert run.config_dir == run.run_dir / "config"


def test_the_composed_configuration_is_the_authored_record(tmp_path):
    with _open(tmp_path) as run:
        composed = yaml.safe_load((run.config_dir / "composed.yaml").read_text())
        resolved = yaml.safe_load((run.config_dir / "resolved.yaml").read_text())
    assert "runtime" not in composed  # nothing was authored there
    assert resolved["runtime"]["config_hash"].startswith("sha256:")


def test_a_deferred_hash_is_absent_from_the_first_wave(tmp_path):
    with _open(tmp_path) as run:
        assert not (run.config_dir / "hashes" / "dataset_hash.json").exists()
        assert run.deferred == ("dataset_hash",)


# --------------------------------------------------------------------------- #
# wave two — deferred hashes completed mid-run
# --------------------------------------------------------------------------- #


def test_completing_a_deferred_hash_writes_its_artifact_and_records_it(tmp_path):
    with _open(tmp_path) as run:
        entries = run.complete_hash(
            "dataset_hash", inputs={"rows": 50_000}, provenance="manifest_content"
        )
        assert (run.config_dir / "hashes" / "dataset_hash.json").is_file()
        assert run.manifest.data["hashes"]["dataset_hash"]["value"] == (
            entries["dataset_hash"].value
        )
        assert run.manifest.data["hashes"]["dataset_hash"]["provenance"] == "manifest_content"
        assert run.deferred == ()


def test_completing_a_hash_that_was_not_deferred_is_refused(tmp_path):
    with _open(tmp_path) as run:
        with pytest.raises(RefusalError, match="was not deferred"):
            run.complete_hash("config_hash")


def test_an_interruption_after_a_deferred_hash_keeps_it(tmp_path):
    """The reason the bundle is three waves and not one atomic persist."""
    with pytest.raises(KeyboardInterrupt):
        with _open(tmp_path) as run:
            run.complete_hash("dataset_hash", inputs={"rows": 10})
            run_dir = run.run_dir
            raise KeyboardInterrupt

    manifest = Manifest.load(run_dir)
    assert manifest.state == "interrupted"
    assert "dataset_hash" in manifest.data["hashes"]


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #


def test_steps_record_their_boundaries(tmp_path):
    with _open(tmp_path) as run:
        with run.step("prepare"):
            pass
        with run.step("fit"):
            pass
    events = [entry["event"] for entry in run.manifest.data["timeline"]]
    assert "step:prepare:completed" in events
    assert "step:fit:started" in events


def test_an_undeclared_step_is_refused(tmp_path):
    with _open(tmp_path) as run:
        with pytest.raises(DeclarationError, match="declares no step 'typo'"):
            with run.step("typo"):
                pass


def test_nesting_is_refused(tmp_path):
    with _open(tmp_path) as run:
        with pytest.raises(RefusalError, match="cannot nest"):
            with run.step("prepare"):
                with run.step("fit"):
                    pass


def test_a_failing_step_fails_the_run_and_stops_the_sequence(tmp_path):
    ran = []
    with pytest.raises(ValueError):
        with _open(tmp_path) as run:
            with run.step("prepare"):
                ran.append("prepare")
            with run.step("fit"):
                ran.append("fit")
                raise ValueError("bad shape")
            with run.step("evaluate"):  # pragma: no cover - must not run
                ran.append("evaluate")

    assert ran == ["prepare", "fit"]
    manifest = Manifest.load(run.run_dir)
    assert manifest.state == "failed"
    assert manifest.data["failure"] == {"kind": "ValueError", "message": "bad shape"}
    assert manifest.completed_steps() == {"prepare"}


def test_progress_and_artifacts_are_empty_on_a_fresh_run(tmp_path):
    with _open(tmp_path) as run:
        assert run.progress == {}
        assert run.artifact("checkpoint") is None
        assert run.best is None


def test_partial_writes_of_a_failed_step_are_left_in_place(tmp_path):
    """Deleting evidence on the way out would be hostile."""
    with pytest.raises(ValueError):
        with _open(tmp_path) as run:
            with run.step("fit"):
                ckpt = run.config.training_outputs.checkpoints.dir
                ckpt.mkdir(parents=True, exist_ok=True)
                (ckpt / "half.pt").write_text("partial")
                raise ValueError("boom")
    assert (run.config.training_outputs.checkpoints.dir / "half.pt").is_file()


# --------------------------------------------------------------------------- #
# resume
# --------------------------------------------------------------------------- #


def _interrupt_at_epoch_10(tmp_path):
    """A run that gets to epoch 10 of 50 and takes a CTRL-C."""
    with pytest.raises(KeyboardInterrupt):
        with _open(tmp_path) as run:
            with run.step("prepare"):
                pass
            with run.step("fit"):
                ckpt = run.config.training_outputs.checkpoints.dir
                ckpt.mkdir(parents=True, exist_ok=True)
                (ckpt / "last.pt").write_text("epoch-10 weights")
                run.record_artifact("checkpoint", "checkpoints/last.pt")
                run.record_progress(epoch=10, step=3200)
                raise KeyboardInterrupt
    return run


def test_resuming_fit_keeps_the_checkpoint_it_resumes_from(tmp_path):
    """The whole point. Clearing fit's scope would turn "continue from epoch 10"
    into "start over", silently destroying the work the resume exists to save."""
    _interrupt_at_epoch_10(tmp_path)

    seen = {}
    with _open(tmp_path, resume=True) as second:
        for name in ("prepare", "fit"):
            if second.skip(name):
                continue
            with second.step(name):
                seen["from_epoch"] = second.progress.get("epoch")
                seen["checkpoint"] = second.artifact("checkpoint")

    assert seen["from_epoch"] == 10
    assert seen["checkpoint"].read_text() == "epoch-10 weights"
    assert second.manifest.state == "completed"


def test_resume_skips_steps_already_completed(tmp_path):
    _interrupt_at_epoch_10(tmp_path)
    ran = []
    with _open(tmp_path, resume=True) as second:
        for name in ("prepare", "fit"):
            if second.skip(name):
                continue
            with second.step(name):
                ran.append(name)
    assert ran == ["fit"]  # prepare was already complete


def test_a_restart_step_discards_its_spoiled_output(tmp_path):
    """The opposite case: a half-written manifest is not resume state."""
    with pytest.raises(ValueError):
        with _open(tmp_path) as run:
            with run.step("evaluate"):
                figures = run.config.training_outputs.figures.dir
                figures.mkdir(parents=True, exist_ok=True)
                (figures / "half.png").write_text("partial")
                raise ValueError("boom")

    with _open(tmp_path, resume=True) as second:
        with second.step("evaluate"):
            assert list(second.config.training_outputs.figures.dir.iterdir()) == []


def test_a_run_can_be_initialized_now_and_worked_later(tmp_path):
    """Set the directory up now, train in tonight's allocation. The same resume
    mechanism, not a special case: `initialized` is resumable."""
    run = initialize_run(
        _resolution(tmp_path), composed=COMPOSED, hashes=HASHES, argv_text="train --init"
    )
    assert run.manifest.state == "initialized"
    assert (run.config_dir / "resolved.yaml").is_file()

    ran = []
    with _open(tmp_path, resume=True) as second:
        for name in ("prepare", "fit"):
            if second.skip(name):
                continue
            with second.step(name):
                ran.append(name)
    assert ran == ["prepare", "fit"]


def test_resume_appends_a_resumed_event(tmp_path):
    with pytest.raises(ValueError):
        with _open(tmp_path):
            raise ValueError("boom")
    with _open(tmp_path, resume=True) as second:
        pass
    events = [entry["event"] for entry in second.manifest.data["timeline"]]
    assert "resumed" in events


def test_resuming_a_completed_run_is_refused(tmp_path):
    with _open(tmp_path) as run:
        for name in run.command.required_steps:
            with run.step(name):
                pass
    with pytest.raises(RefusalError, match="cannot resume a completed run"):
        with _open(tmp_path, resume=True):
            pass


def test_resume_without_a_prior_run_is_refused(tmp_path):
    with pytest.raises(RefusalError, match="nothing to resume"):
        with _open(tmp_path, resume=True):
            pass


# --------------------------------------------------------------------------- #
# collision
# --------------------------------------------------------------------------- #


def test_a_second_fresh_run_onto_the_same_directory_is_refused(tmp_path):
    with _open(tmp_path):
        pass
    with pytest.raises(RefusalError, match="not empty"):
        with _open(tmp_path):
            pass


def test_clobber_replaces_it(tmp_path):
    with _open(tmp_path) as first:
        first.record_artifact("checkpoint", "checkpoints/last.pt")
    with _open(tmp_path, clobber=True) as second:
        assert second.manifest.data["artifacts"] == {}


# --------------------------------------------------------------------------- #
# augmenting
# --------------------------------------------------------------------------- #


def test_an_augmenting_command_appends_to_the_existing_manifest(tmp_path):
    with _open(tmp_path) as first:
        first.record_artifact("checkpoint", "checkpoints/last.pt")
        run_dir = first.run_dir

    with _open(
        tmp_path, command=EVAL, argv="eval --resolved runs/reef-v3/run",
        existing_target=run_dir,
    ) as second:
        assert second.run_dir == run_dir
        # the training artifact survives an eval
        assert second.manifest.data["artifacts"]["checkpoint"] == "checkpoints/last.pt"

    manifest = Manifest.load(run_dir)
    assert [entry["command"] for entry in manifest.data["invocations"]] == [
        ["train"],
        ["eval"],
    ]
    assert manifest.data["command"] == ["train"]  # the producing command


def test_clobbering_a_leaf_from_an_augmenting_command_spares_the_checkpoints(tmp_path):
    with _open(tmp_path) as first:
        ckpt = first.config.training_outputs.checkpoints.dir
        ckpt.mkdir(parents=True, exist_ok=True)
        (ckpt / "last.pt").write_text("weights")
        figures = first.config.training_outputs.figures.dir
        figures.mkdir(parents=True, exist_ok=True)
        (figures / "stale.png").write_text("old")
        run_dir = first.run_dir

    with _open(
        tmp_path, command=EVAL, existing_target=run_dir, clobber=True, argv="eval"
    ) as second:
        assert list(second.config.training_outputs.figures.dir.iterdir()) == []
        assert (second.config.training_outputs.checkpoints.dir / "last.pt").is_file()


# --------------------------------------------------------------------------- #
# configuration indices
# --------------------------------------------------------------------------- #


def test_an_identical_configuration_reuses_its_index(tmp_path):
    with _open(tmp_path) as first:
        run_dir = first.run_dir
    for _ in range(2):
        with _open(tmp_path, command=EVAL, existing_target=run_dir, argv="eval"):
            pass
    manifest = Manifest.load(run_dir)
    assert [entry["config"] for entry in manifest.data["invocations"]] == [1, 1, 1]
    assert not (run_dir / "config" / "2").exists()


def test_an_overridden_configuration_gets_its_own_index_and_bundle(tmp_path):
    with _open(tmp_path) as first:
        run_dir = first.run_dir

    overridden = {**COMPOSED, "epochs": 50}
    with _open(
        tmp_path, overridden, command=EVAL, existing_target=run_dir,
        argv="eval epochs=50",
    ) as second:
        assert second.config_dir == config_dir_for(run_dir, 2)

    manifest = Manifest.load(run_dir)
    assert [entry["config"] for entry in manifest.data["invocations"]] == [1, 2]
    assert (run_dir / "config" / "2" / "resolved.yaml").is_file()
    assert (run_dir / "config" / "resolved.yaml").is_file()
    first_hash = manifest.data["invocations"][0]["config_hash"]
    assert manifest.data["invocations"][1]["config_hash"] != first_hash


# --------------------------------------------------------------------------- #
# artifacts and lineage
# --------------------------------------------------------------------------- #


def test_an_absolute_artifact_path_is_stored_relative_to_the_run(tmp_path):
    with _open(tmp_path) as run:
        run.record_artifact("checkpoint", run.run_dir / "checkpoints" / "last.pt")
    assert run.manifest.data["artifacts"]["checkpoint"] == "checkpoints/last.pt"


def test_lineage_is_recorded_when_continuing(tmp_path):
    with _open(tmp_path) as first:
        parent_dir = first.run_dir
        parent_hash = first.resolution.digests["config_hash"]

    composed = {**COMPOSED, "epochs": 50}
    with _open(
        tmp_path, composed, argv="train --continue-from ... epochs=50", clobber=True,
        parent={
            "run_dir": str(parent_dir),
            "config_hash": parent_hash,
            "artifact": "checkpoints/last.pt",
        },
    ) as child:
        assert child.manifest.data["parent"]["config_hash"] == parent_hash


def test_a_targetless_command_cannot_open_a_run(tmp_path):
    inspect = CommandSpec(name=("inspect", "config"), hashes=("config_hash",))
    resolution = _resolution(tmp_path, command=inspect)
    with pytest.raises(RefusalError, match="no target, so it has no run"):
        with open_run(resolution, composed=COMPOSED, hashes=HASHES, argv_text="x"):
            pass


def test_the_manifest_is_the_only_json_at_the_top_of_the_run(tmp_path):
    """One file, both jobs: the state record and the resource index. cli.txt is gone."""
    with _open(tmp_path) as run:
        pass
    tops = sorted(p.name for p in run.run_dir.iterdir())
    assert tops == ["config", MANIFEST_NAME]


def test_a_restart_step_cannot_clear_outside_the_containment_root(tmp_path):
    """Every path the framework clears is containment-checked.

    This was the one destructive path that went straight to clear_contents:
    prepare_target and prepare_scope both validate, so a step scope resolving
    outside the containment root was the only way to make the framework delete
    something the configuration never authorized.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")

    class Escaping(TrainingOutputs):
        figures: OutputLeaf = Field(
            default_factory=lambda: OutputLeaf(dir=outside.resolve())
        )

    class RootEscaping(Root):
        training_outputs: Escaping = Field(default_factory=Escaping)

    resolution = resolve(
        COMPOSED,
        root_type=RootEscaping,
        command=TRAIN,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
    )
    initialize_run(
        resolution, composed=COMPOSED, hashes=HASHES, argv_text="train --init"
    )

    with pytest.raises(RefusalError, match="outside its containment root"):
        with open_run(
            resolution,
            composed=COMPOSED,
            hashes=HASHES,
            argv_text="train --resume",
            resume=True,
        ) as run:
            with run.step("evaluate"):
                pass

    assert (outside / "precious.txt").read_text() == "keep"


# --------------------------------------------------------------------------- #
# completion is declared, not assumed (D25)
# --------------------------------------------------------------------------- #


def test_returning_with_required_steps_outstanding_is_partial(tmp_path):
    """Returning is not completion. Otherwise running part of a stepped command is
    indistinguishable from running all of it, and lands in the one state that
    cannot be resumed."""
    with _open(tmp_path) as run:
        with run.step("prepare"):
            pass
    assert run.manifest.state == "partial"
    assert run.outstanding_steps() == ("fit",)


def test_a_partial_run_is_resumable(tmp_path):
    with _open(tmp_path) as first:
        with first.step("prepare"):
            pass
    with _open(tmp_path, resume=True) as second:
        with second.step("fit"):
            pass
    assert second.manifest.state == "completed"


def test_an_optional_step_does_not_hold_the_run_open(tmp_path):
    """`evaluate` is declared but outside the required set."""
    with _open(tmp_path) as run:
        for name in ("prepare", "fit"):
            with run.step(name):
                pass
    assert "evaluate" not in run.command.required_steps
    assert run.manifest.state == "completed"


def test_a_required_step_may_be_declared_not_applicable(tmp_path):
    """Satisfied, and recorded with its reason — so deliberate omission and a
    forgotten branch stay distinguishable."""
    with _open(tmp_path) as run:
        with run.step("prepare"):
            pass
        run.not_applicable("fit", reason="weights supplied, nothing to train")
    assert run.manifest.state == "completed"
    entry = next(
        e for e in run.manifest.data["timeline"] if e["event"] == "step:fit:not_applicable"
    )
    assert entry["reason"] == "weights supplied, nothing to train"


def test_a_completed_step_cannot_be_declared_not_applicable(tmp_path):
    with pytest.raises(RefusalError, match="already completed"):
        with _open(tmp_path) as run:
            with run.step("prepare"):
                pass
            run.not_applicable("prepare", reason="nope")


def test_a_step_completed_in_an_earlier_invocation_still_counts(tmp_path):
    """Completion asks the manifest, not this process — which is what makes
    resume-with-skip reach completed at all."""
    _interrupt_at_epoch_10(tmp_path)
    with _open(tmp_path, resume=True) as second:
        for name in ("prepare", "fit"):
            if second.skip(name):
                continue
            with second.step(name):
                pass
    assert second.manifest.state == "completed"


def test_an_augmenting_command_cannot_un_complete_a_run(tmp_path):
    """A failed evaluation has not un-trained the run."""
    with _open(tmp_path) as run:
        for name in run.command.required_steps:
            with run.step(name):
                pass
    assert run.manifest.state == "completed"

    target = run.run_dir
    with pytest.raises(ValueError):
        with _open(tmp_path, command=EVAL, existing_target=target):
            raise ValueError("evaluation blew up")

    reopened = Manifest.load(target)
    assert reopened.state == "completed"
    events = [e["event"] for e in reopened.data["timeline"]]
    assert "invocation:failed" in events and "error" not in events


def test_a_non_advancing_command_leaves_the_state_alone(tmp_path):
    """The generalization of the augment case: whether running a command is work
    on the run is declared, not inferred from its mode."""
    from dataclasses import replace

    prepare = replace(TRAIN, name=("prepare",), steps=(), advances_run=False)
    resolution = _resolution(tmp_path, command=prepare)
    with open_run(
        resolution, composed=COMPOSED, hashes=HASHES, argv_text="prepare"
    ) as run:
        pass

    assert run.manifest.state == "initialized"
    events = [entry["event"] for entry in run.manifest.data["timeline"]]
    assert events == ["initialized", "invocation:started", "invocation:finished"]
    assert "started" not in events and "finished" not in events


def test_a_prepared_run_is_worked_by_a_later_invocation(tmp_path):
    from dataclasses import replace

    prepare = replace(TRAIN, name=("prepare",), steps=(), advances_run=False)
    with open_run(
        _resolution(tmp_path, command=prepare),
        composed=COMPOSED,
        hashes=HASHES,
        argv_text="prepare",
    ):
        pass

    with _open(tmp_path, resume=True) as second:
        for name in second.command.required_steps:
            with second.step(name):
                pass
    assert second.manifest.state == "completed"


def test_augmenting_never_advances_a_run_whatever_is_declared(tmp_path):
    """Not a project's call: an augmenting command adds to a run someone else
    produced, so it cannot speak to whether that run's work happened."""
    from dataclasses import replace

    assert replace(EVAL, advances_run=True).advances_run is False
