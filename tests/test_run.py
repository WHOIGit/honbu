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
    OutputBlock,
    OutputLeaf,
    PortablePath,
    RefusalError,
    ResolvedString,
    RuntimeConfig,
    SourceHash,
    StrictModel,
)
from honbu.core import MANIFEST_NAME, TemplateContext
from honbu.errors import DeclarationError
from honbu.flow import (
    CommandSpec,
    ComposeSpec,
    Manifest,
    ProjectSpec,
    StepSpec,
    TargetSpec,
    config_dir_for,
    initialize_run,
    open_run,
    parent_record,
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
    note: str | None = None


class Data(StrictModel):
    uri: PortablePath
    prepared: Prepared = Field(default_factory=Prepared)


class TrainingOutputs(OutputBlock):
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
    config_sources=("resolved", "fork"),
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


def test_run_hash_and_hash_entry_across_deferred_fanned_and_resume(tmp_path):
    from honbu import FannedHash

    hashes = HashRegistry(
        [
            FieldHash(name="config_hash", include=["experiment", "data", "epochs"]),
            SourceHash(
                name="dataset_hash",
                build=lambda ctx: {"version": "1", "rows": ctx.get("rows", 0)},
                reads_inputs=True,
            ),
            FannedHash(
                name="head_hash",
                build=lambda ctx: {
                    "dorsal": {"version": "1", "c": 3},
                    "caudal": {"version": "1", "c": 5},
                },
                reads_inputs=True,
            ),
        ],
        root_type=Root,
    )
    cmd = CommandSpec(
        name=("train",),
        target=TARGET,
        hashes=("config_hash", "dataset_hash", "head_hash"),
        steps=(StepSpec("prepare"), StepSpec("fit")),
    )
    res = resolve(COMPOSED, root_type=Root, command=cmd, hashes=hashes, context=CONTEXT, cwd=tmp_path)

    with open_run(res, composed=COMPOSED, hashes=hashes, argv_text="train") as first:
        assert first.hash("config_hash") == res.hashes["config_hash"].value
        assert first.hash_entry("config_hash") == res.hashes["config_hash"]
        assert first.hash("dataset_hash") is None
        assert first.hash_entry("dataset_hash") is None

        with first.step("prepare"):
            ds = first.complete_hash("dataset_hash", inputs={"rows": 99})
            heads = first.complete_hash("head_hash")
            assert first.hash("dataset_hash") == ds["dataset_hash"].value
            assert first.hash_entry("dataset_hash") == ds["dataset_hash"]
            assert first.hash("head_hash.dorsal") == heads["head_hash.dorsal"].value
            assert first.hash_entry("head_hash.caudal") == heads["head_hash.caudal"]
            assert first.deferred == ()

    # Resume sees previously completed deferred and fanned hashes.
    with open_run(res, composed=COMPOSED, hashes=hashes, argv_text="train --resume", resume=True) as second:
        assert second.hash("dataset_hash") == ds["dataset_hash"].value
        assert second.hash_entry("dataset_hash") == ds["dataset_hash"]
        assert second.hash("head_hash.dorsal") == heads["head_hash.dorsal"].value
        assert second.deferred == ()
        with second.step("fit"):
            pass


def test_run_artifact_preserves_windows_absolute_path(tmp_path):
    with _open(tmp_path) as run:
        run.manifest.record_artifact("win_ckpt", "C:\\models\\best.pt")
        assert str(run.artifact("win_ckpt")) == "C:\\models\\best.pt"


def test_run_lifecycle_hardening_and_manifest_invariants(tmp_path):
    # 1. clobber=True and resume=True are mutually exclusive
    with pytest.raises(RefusalError, match="mutually exclusive"):
        with _open(tmp_path, clobber=True, resume=True):
            pass

    # 2. resume=True on augmenting command is refused
    with _open(tmp_path) as first:
        with first.step("prepare"):
            pass
        run_dir = first.run_dir
    with pytest.raises(RefusalError, match="augmenting command and cannot use resume"):
        with _open(tmp_path, command=EVAL, existing_target=run_dir, resume=True):
            pass

    # 3. Run.warnings preserves resolution + target warnings (case collision)
    (tmp_path / "runs" / "reef-v3" / "Run").mkdir(parents=True, exist_ok=True)
    res_warn = resolve(
        {"experiment": {"name": "reef v3"}, "data": {"uri": "./datasets/coco8"}},
        root_type=Root,
        command=TRAIN,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
    )
    with open_run(res_warn, composed=COMPOSED, hashes=HASHES, clobber=True) as r:
        assert any("differ only in case" in w for w in r.warnings)


def test_run_skip_respects_not_applicable_steps(tmp_path):
    with _open(tmp_path) as first:
        first.not_applicable("prepare", reason="cached upstream")
    assert first.manifest.state == "partial"

    with _open(tmp_path, resume=True) as second:
        assert second.skip("prepare") is True
        assert second.skip("fit") is False
        with second.step("fit"):
            pass
    assert second.manifest.state == "completed"


def test_manifest_failure_timeline_derive_state_and_progress_merge(tmp_path):
    with pytest.raises(ValueError, match="oom"):
        with _open(tmp_path) as first:
            first.record_progress(epoch=1, loss=0.9)
            first.record_progress({"lr": 0.01})
            assert first.progress["epoch"] == 1
            assert first.progress["loss"] == 0.9
            assert first.progress["lr"] == 0.01
            first.set_progress(epoch=2)
            assert "loss" not in first.progress
            assert first.progress["epoch"] == 2
            run_dir = first.run_dir
            raise ValueError("oom")

    failed = Manifest.load(run_dir)
    err_event = failed.data["timeline"][-1]
    assert err_event["event"] == "error"
    assert err_event["kind"] == "ValueError"
    assert err_event["message"] == "oom"
    assert failed.data["failure"] == {"kind": "ValueError", "message": "oom"}

    # Tampering top-level state on disk is corrected by derive_state=True (and kept when False)
    import json

    raw = json.loads((run_dir / MANIFEST_NAME).read_text())
    raw["state"] = "completed"
    (run_dir / MANIFEST_NAME).write_text(json.dumps(raw))
    assert Manifest.load(run_dir, derive_state=True).state == "failed"
    assert Manifest.load(run_dir, derive_state=False).state == "completed"

    # Resuming clears top-level failure while retaining historical error event on timeline
    with _open(tmp_path, resume=True) as second:
        assert second.manifest.data["failure"] is None
        for step in second.command.required_steps:
            with second.step(step):
                pass
    reloaded = Manifest.load(run_dir)
    assert reloaded.state == "completed"
    assert reloaded.data["failure"] is None
    assert any(e["event"] == "error" and e["kind"] == "ValueError" for e in reloaded.data["timeline"])


def test_augmenting_config_index_gt_1_preserves_producing_run_hashes(tmp_path):
    extra_hashes = HashRegistry(
        [
            FieldHash(name="config_hash", include=["experiment", "data", "epochs"]),
            FieldHash(name="eval_hash", include=["epochs"]),
        ],
        root_type=Root,
    )
    eval_cmd = CommandSpec(
        name=("eval",),
        mode="augment",
        target=TARGET,
        config_sources=("resolved",),
        scope=("training_outputs.figures",),
        hashes=("config_hash", "eval_hash"),
    )
    with _open(tmp_path) as first:
        for s in first.command.required_steps:
            with first.step(s):
                pass
        run_dir = first.run_dir
        producing_hash = first.hash("config_hash")

    overridden = {**COMPOSED, "epochs": 99}
    res2 = resolve(
        overridden,
        root_type=Root,
        command=eval_cmd,
        hashes=extra_hashes,
        context=CONTEXT,
        cwd=tmp_path,
        existing_target=run_dir,
    )
    with open_run(res2, composed=overridden, hashes=extra_hashes, argv_text="eval epochs=99") as aug:
        assert aug.config_dir == config_dir_for(run_dir, 2)
        assert aug.hash("config_hash") == res2.digests["config_hash"]
        assert aug.hash("config_hash") != producing_hash

    manifest = Manifest.load(run_dir)
    # Producing run's config_hash stays intact at top-level; newly introduced eval_hash is added!
    assert manifest.data["hashes"]["config_hash"]["value"] == producing_hash
    assert manifest.data["hashes"]["eval_hash"]["value"] == res2.digests["eval_hash"]
    assert manifest.data["invocations"][1]["config_hash"] == res2.digests["config_hash"]
    assert (run_dir / "config" / "2" / "hashes" / "config_hash.json").is_file()


def test_resume_preserves_run_id_and_refuses_config_hash_drift_by_default(tmp_path):
    ctx1 = TemplateContext(
        now_local=CONTEXT.now_local, now_utc=CONTEXT.now_utc, seed="1111111111111111"
    )
    ctx2 = TemplateContext(
        now_local=CONTEXT.now_local, now_utc=CONTEXT.now_utc, seed="2222222222222222"
    )
    res1 = resolve(COMPOSED, root_type=Root, command=TRAIN, hashes=HASHES, context=ctx1, cwd=tmp_path)
    with open_run(res1, composed=COMPOSED, hashes=HASHES, argv_text="train") as first:
        with first.step("prepare"):
            pass
        first_run_id = first.config.runtime.run_id
        run_dir = first.run_dir

    # Resuming with a fresh context seed preserves run_id and reuses config index 1!
    res2 = resolve(COMPOSED, root_type=Root, command=TRAIN, hashes=HASHES, context=ctx2, cwd=tmp_path)
    assert res2.config.runtime.run_id != first_run_id
    with open_run(res2, composed=COMPOSED, hashes=HASHES, argv_text="train --resume", resume=True) as second:
        assert second.config.runtime.run_id == first_run_id
        assert second.config_dir == config_dir_for(run_dir, 1)
    assert not (run_dir / "config" / "2").exists()

    # Behavioral config_hash drift is refused by default on resume
    drifted_cfg = {**COMPOSED, "epochs": 999}
    drifted_res = resolve(
        drifted_cfg, root_type=Root, command=TRAIN, hashes=HASHES, context=ctx2, cwd=tmp_path
    )
    with pytest.raises(RefusalError, match="behavioral config_hash changed"):
        with open_run(drifted_res, composed=drifted_cfg, hashes=HASHES, resume=True):
            pass

    # Opt-in allow_config_drift=True permits it
    with open_run(
        drifted_res,
        composed=drifted_cfg,
        hashes=HASHES,
        resume=True,
        allow_config_drift=True,
    ) as forced:
        with forced.step("fit"):
            pass
    assert forced.manifest.state == "completed"


def test_disabled_output_leaf_is_skipped_in_scope_dirs(tmp_path):
    with _open(tmp_path) as first:
        run_dir = first.run_dir

    disabled = {**COMPOSED, "training_outputs": {"figures": {"enabled": False, "dir": "figures"}}}
    res = resolve(
        disabled,
        root_type=Root,
        command=EVAL,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
        existing_target=run_dir,
    )
    with open_run(res, composed=disabled, hashes=HASHES, clobber=True):
        pass
    assert not (run_dir / "figures").exists()


def test_parameterized_config_hash_name_and_parent_record_artifact_key(tmp_path):
    class CustomRuntime(RuntimeConfig):
        cfg_digest: Annotated[ResolvedString, FromHash("my_cfg")] = None

    class CustomRoot(Root):
        runtime: CustomRuntime = Field(default_factory=CustomRuntime)

    custom_hashes = HashRegistry(
        [FieldHash(name="my_cfg", include=["experiment", "epochs"])],
        root_type=CustomRoot,
    )
    project = ProjectSpec(
        root_type=CustomRoot,
        compose_spec=ComposeSpec(packaged=None),
        hashes=custom_hashes,
        config_hash_name="my_cfg",
    )
    cmd = CommandSpec(name=("train",), target=TARGET, hashes=("my_cfg",), fork_artifact="last")
    res = resolve(COMPOSED, command=cmd, project=project, context=CONTEXT, cwd=tmp_path)
    with open_run(res, composed=COMPOSED, project=project, argv_text="train") as first:
        first.record_artifact("last", "checkpoints/last.pt")
        first.record_artifact("best", "checkpoints/best.pt")
        run_dir = first.run_dir

    assert Manifest.load(run_dir).data["invocations"][0]["config_hash"] == res.digests["my_cfg"]

    rec_default = parent_record(run_dir, command=cmd, project=project)
    assert rec_default["artifact_key"] == "last"
    assert rec_default["artifact"] == "checkpoints/last.pt"
    assert rec_default["config_hash"] == res.digests["my_cfg"]

    rec_best = parent_record(run_dir, artifact="best", command=cmd, project=project)
    assert rec_best["artifact_key"] == "best"
    assert rec_best["artifact"] == "checkpoints/best.pt"


# --------------------------------------------------------------------------- #
# opt-in config/cli.txt
# --------------------------------------------------------------------------- #


def test_cli_txt_is_not_written_by_default(tmp_path):
    with _open(tmp_path, argv="train epochs=5") as run:
        assert not (run.config_dir / "cli.txt").exists()


def test_cli_txt_is_written_atomically_when_opted_in(tmp_path):
    with _open(tmp_path, argv="train data=nes-hf", write_cli_txt=True) as run:
        cli = run.config_dir / "cli.txt"
        # Present before work starts, beside the rest of the bundle.
        assert cli.read_text() == "train data=nes-hf\n"
        assert (run.config_dir / "composed.yaml").is_file()
    assert not list(run.config_dir.glob("*.tmp"))


def test_cli_txt_is_omitted_for_empty_argv(tmp_path):
    with _open(tmp_path, argv="   ", write_cli_txt=True) as run:
        assert not (run.config_dir / "cli.txt").exists()


def test_cli_txt_defaults_from_the_project_and_explicit_false_wins(tmp_path):
    project = ProjectSpec(
        root_type=Root, compose_spec=ComposeSpec(), hashes=HASHES, write_cli_txt=True
    )
    assert (
        ProjectSpec(root_type=Root, compose_spec=ComposeSpec(), hashes=HASHES).write_cli_txt
        is False
    )
    with _open(tmp_path, argv="train", project=project) as run:
        assert (run.config_dir / "cli.txt").read_text() == "train\n"

    other = tmp_path / "other"
    other.mkdir()
    with _open(other, argv="train", project=project, write_cli_txt=False) as run:
        assert not (run.config_dir / "cli.txt").exists()


def test_cli_txt_keeps_the_invocation_that_produced_each_configuration(tmp_path):
    with _open(tmp_path, argv="train", write_cli_txt=True) as first:
        run_dir = first.run_dir
    with _open(
        tmp_path, command=EVAL, existing_target=run_dir, argv="eval", write_cli_txt=True
    ):
        pass
    assert (run_dir / "config" / "cli.txt").read_text() == "train\n"

    overridden = {**COMPOSED, "epochs": 50}
    with _open(
        tmp_path, overridden, command=EVAL, existing_target=run_dir,
        argv="eval epochs=50", write_cli_txt=True,
    ) as second:
        assert (second.config_dir / "cli.txt").read_text() == "eval epochs=50\n"
