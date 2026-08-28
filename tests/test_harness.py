"""The harness: a whole project, driven from argv.

This is the closest thing to an adoption test. A miniature project declares its
schema, hashes and commands, registers work functions, and everything below is
driven the way a user would drive it — through tokens.
"""

from __future__ import annotations

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
    ResolvedPath,
    ResolvedString,
    RuntimeConfig,
    SourceHash,
    StrictModel,
)
from honbu.errors import DeclarationError
from honbu.flow import (
    Application,
    CommandSpec,
    ComposeSpec,
    Manifest,
    Positional,
    StepSpec,
    TargetSpec,
)

# --------------------------------------------------------------------------- #
# a miniature project
# --------------------------------------------------------------------------- #


class Runtime(RuntimeConfig):
    config_hash: Annotated[ResolvedString, FromHash("config_hash")] = None


class Data(StrictModel):
    uri: PortablePath
    dataset_hash: Annotated[ResolvedString, FromHash("dataset_hash")] = None


class TrainingOutputs(OutputBlock):
    dir: ResolvedPath = None
    dir_template: str | None = "{experiment.name:slug}/{runtime.config_hash:coolname}"
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
    "training_outputs.dir_template", "training_outputs.dir", "output_root"
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

TRAIN = CommandSpec(
    name=("train",),
    target=TARGET,
    hashes=("config_hash", "dataset_hash"),
    continue_artifact="checkpoint",
    steps=(
        StepSpec("prepare", on_resume="restart"),
        StepSpec("fit", scope=("training_outputs.checkpoints",)),
    ),
)

#: The declared spelling of "set the run up now, work it tonight". It declares no
#: steps and does not advance the run, so it leaves the directory initialized and
#: resumable — no framework flag, and no borrowing another command's step list.
PREPARE = CommandSpec(
    name=("prepare",),
    target=TARGET,
    hashes=("config_hash",),
    config_sources=("default", "file"),
    advances_run=False,
)

EVAL = CommandSpec(
    name=("eval",),
    mode="augment",
    target=TARGET,
    config_sources=("resolved",),
    scope=("training_outputs.figures",),
    hashes=("config_hash",),
)

INSPECT_CONFIG = CommandSpec(
    name=("inspect", "config"), config_sources=("default", "file"),
    hashes=("config_hash", "dataset_hash"),
)

INSPECT_CHECKPOINT = CommandSpec(
    name=("inspect", "checkpoint"),
    config_sources=(),
    positionals=(Positional("checkpoint"),),
)


def build_app(record: list[str]) -> Application:
    app = Application(
        name="mini",
        root_type=Root,
        hashes=HASHES,
        compose_spec=ComposeSpec(packaged=None, local="configs"),
    )

    @app.command(TRAIN, help="train a model")
    def train(run):
        record.append(f"train:{run.run_dir.name}")
        for name in ("prepare", "fit"):
            if run.skip(name):
                record.append(f"skip:{name}")
                continue
            with run.step(name):
                if name == "prepare":
                    run.complete_hash("dataset_hash", inputs={"rows": 42})
                else:
                    ckpt = run.config.training_outputs.checkpoints.dir
                    ckpt.mkdir(parents=True, exist_ok=True)
                    (ckpt / "last.pt").write_text("weights")
                    run.record_artifact("checkpoint", "checkpoints/last.pt")
                    run.record_best(epoch=7, metrics={"map50": 0.6})

    @app.command(PREPARE, help="create the run directory without working it")
    def prepare_only(run):
        record.append(f"prepare:{run.run_dir.name}")

    @app.command(EVAL, help="evaluate a finished run")
    def evaluate(run):
        figures = run.config.training_outputs.figures.dir
        figures.mkdir(parents=True, exist_ok=True)
        (figures / "pr.png").write_text("plot")
        record.append(f"eval:{run.run_dir.name}")

    @app.command(INSPECT_CONFIG, help="print identities")
    def inspect_config(resolution):
        record.append(f"inspect:{resolution.digests['config_hash'][:14]}")
        record.append(f"deferred:{','.join(resolution.deferred)}")

    @app.command(INSPECT_CHECKPOINT, help="describe a checkpoint")
    def inspect_checkpoint(parsed):
        # config_sources=() means there is nothing to resolve, so the handler
        # receives the invocation itself.
        record.append(f"checkpoint-inspected:{parsed.positionals['checkpoint']}")

    return app


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "config.yaml").write_text(
        "experiment:\n  name: reef v3\ndata:\n  uri: ./datasets/coco8\nepochs: 10\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #


def test_train_from_argv_produces_a_complete_run(project):
    record: list[str] = []
    code = build_app(record).dispatch(["train"])
    assert code == 0

    runs = list((project / "runs" / "reef-v3").iterdir())
    assert len(runs) == 1
    manifest = Manifest.load(runs[0])
    assert manifest.state == "completed"
    assert manifest.data["artifacts"]["checkpoint"] == "checkpoints/last.pt"
    assert manifest.data["best"]["metrics"] == {"map50": 0.6}
    assert manifest.data["hashes"]["dataset_hash"]["value"].startswith("sha256:")
    assert manifest.data["invocations"][0]["argv"] == "train"


def test_an_override_reaches_the_configuration_and_moves_the_identity(project):
    record: list[str] = []
    app = build_app(record)
    app.dispatch(["train"])
    app.dispatch(["train", "epochs=50"])

    runs = sorted((project / "runs" / "reef-v3").iterdir())
    assert len(runs) == 2  # a different config_hash, so a different directory
    hashes = {Manifest.load(r).data["hashes"]["config_hash"]["value"] for r in runs}
    assert len(hashes) == 2


def test_the_bundle_records_what_was_authored(project):
    build_app([]).dispatch(["train", "epochs=50"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())
    composed = yaml.safe_load((run_dir / "config" / "composed.yaml").read_text())
    assert composed["epochs"] == 50
    assert "runtime" not in composed


def test_a_second_train_onto_the_same_target_refuses_with_an_exit_code(project, capsys):
    app = build_app([])
    assert app.dispatch(["train"]) == 0
    assert app.dispatch(["train"]) == 1
    err = capsys.readouterr().err
    assert "not empty" in err
    assert "completed train run" in err  # the occupant is described
    assert "Traceback" not in err


def test_clobber_replaces_it(project):
    app = build_app([])
    app.dispatch(["train"])
    assert app.dispatch(["train", "--clobber"]) == 0


def test_a_command_can_prepare_a_run_without_working_it(project):
    """What `--init` used to be, now declared by the project (D25).

    The run is left partial rather than completed, so it stays resumable and the
    framework reserves no flag for "how much work to do".
    """
    record: list[str] = []
    assert build_app(record).dispatch(["prepare"]) == 0
    run_dir = next((project / "runs" / "reef-v3").iterdir())
    assert (run_dir / "config" / "resolved.yaml").is_file()
    assert Manifest.load(run_dir).state == "initialized"
    assert record == ["prepare:" + run_dir.name]
    events = [e["event"] for e in Manifest.load(run_dir).data["timeline"]]
    assert "started" not in events  # nothing claims work occurred


def test_a_later_invocation_resumes_a_prepared_run(project):
    record: list[str] = []
    app = build_app(record)
    app.dispatch(["prepare"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())

    assert app.dispatch(["train", "--resume", "--resolved", str(run_dir)]) == 0
    assert Manifest.load(run_dir).state == "completed"
    assert "skip:prepare" not in record


# --------------------------------------------------------------------------- #
# augmenting a finished run
# --------------------------------------------------------------------------- #


def test_eval_augments_the_run_and_leaves_the_checkpoint(project):
    record: list[str] = []
    app = build_app(record)
    app.dispatch(["train"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())

    assert app.dispatch(["eval", "--resolved", str(run_dir)]) == 0
    assert (run_dir / "checkpoints" / "last.pt").is_file()
    assert (run_dir / "figures" / "pr.png").is_file()

    manifest = Manifest.load(run_dir)
    assert [i["command"] for i in manifest.data["invocations"]] == [["train"], ["eval"]]
    assert manifest.data["command"] == ["train"]


def test_eval_twice_reuses_one_configuration_index(project):
    app = build_app([])
    app.dispatch(["train"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())
    app.dispatch(["eval", "--resolved", str(run_dir)])
    app.dispatch(["eval", "--resolved", str(run_dir), "--clobber"])
    manifest = Manifest.load(run_dir)
    assert [i["config"] for i in manifest.data["invocations"]] == [1, 1, 1]


def test_eval_with_an_override_adds_a_configuration_index(project):
    app = build_app([])
    app.dispatch(["train"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())
    assert app.dispatch(["eval", "--resolved", str(run_dir), "epochs=99"]) == 0
    manifest = Manifest.load(run_dir)
    assert [i["config"] for i in manifest.data["invocations"]] == [1, 2]
    assert (run_dir / "config" / "2" / "resolved.yaml").is_file()


def test_eval_cannot_compose_a_fresh_root(project, capsys):
    app = build_app([])
    assert app.dispatch(["eval"]) == 1
    assert "cannot compose a fresh configuration" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# continuing
# --------------------------------------------------------------------------- #


def test_continuing_makes_a_new_run_with_recorded_lineage(project):
    app = build_app([])
    app.dispatch(["train"])
    parent = next((project / "runs" / "reef-v3").iterdir())

    assert app.dispatch(["train", "--continue-from", str(parent), "epochs=50"]) == 0
    runs = sorted((project / "runs" / "reef-v3").iterdir())
    assert len(runs) == 2
    child = next(r for r in runs if r != parent)
    lineage = Manifest.load(child).data["parent"]
    assert lineage["run_dir"] == str(parent)
    assert lineage["artifact"] == "checkpoints/last.pt"


def test_continuing_builds_on_the_authored_form_so_it_survives_a_move(project, monkeypatch, tmp_path):
    """The reason continuation reads composed.yaml. A resolved configuration pins
    input paths absolute, which is correct on the machine that produced it and wrong
    on the node you meant to continue in."""
    app = build_app([])
    app.dispatch(["train"])
    parent = next((project / "runs" / "reef-v3").iterdir())

    resolved = yaml.safe_load((parent / "config" / "resolved.yaml").read_text())
    composed = yaml.safe_load((parent / "config" / "composed.yaml").read_text())
    assert Path(resolved["data"]["uri"]).is_absolute()      # baked to this machine
    assert composed["data"]["uri"] == "./datasets/coco8"    # portable

    # continue from a different working directory, as another machine would
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    assert app.dispatch(["train", "--continue-from", str(parent), "epochs=50"]) == 0

    child = next(
        r for r in (elsewhere / "runs" / "reef-v3").iterdir()
    )
    child_resolved = yaml.safe_load((child / "config" / "resolved.yaml").read_text())
    assert child_resolved["data"]["uri"].startswith(str(elsewhere))


def test_continuing_does_not_inherit_the_parents_directory_or_identity(project):
    app = build_app([])
    app.dispatch(["train"])
    parent = next((project / "runs" / "reef-v3").iterdir())
    parent_hash = Manifest.load(parent).data["hashes"]["config_hash"]["value"]

    app.dispatch(["train", "--continue-from", str(parent), "epochs=50"])
    child = next(r for r in (project / "runs" / "reef-v3").iterdir() if r != parent)
    child_manifest = Manifest.load(child)
    assert child_manifest.data["hashes"]["config_hash"]["value"] != parent_hash
    assert child_manifest.data["parent"]["config_hash"] == parent_hash


def test_continuing_from_a_run_with_no_such_artifact_is_refused(project, capsys):
    app = build_app([])
    app.dispatch(["prepare"])
    parent = next((project / "runs" / "reef-v3").iterdir())
    assert app.dispatch(["train", "--continue-from", str(parent), "epochs=50"]) == 1
    assert "registered no 'checkpoint' artifact" in capsys.readouterr().err


def test_a_command_accepting_continue_must_name_its_artifact(project):
    app = Application(
        name="mini", root_type=Root, hashes=HASHES, compose_spec=ComposeSpec(packaged=None)
    )
    app.command(CommandSpec(name=("train",), target=TARGET))(lambda run: None)
    with pytest.raises(DeclarationError, match="names no artifact to continue from"):
        app.dispatch(["train"])


# --------------------------------------------------------------------------- #
# read-only commands
# --------------------------------------------------------------------------- #


def test_inspect_gets_a_resolution_and_reports_the_deferred_set(project):
    record: list[str] = []
    assert build_app(record).dispatch(["inspect", "config"]) == 0
    assert record[0].startswith("inspect:sha256:")
    assert record[1] == "deferred:dataset_hash"
    assert not (project / "runs").exists()  # nothing was written


def test_a_targetless_command_needs_no_configuration_at_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no configs/ directory whatsoever
    record: list[str] = []
    assert build_app(record).dispatch(["inspect", "checkpoint", "a.ckpt"]) == 0
    assert record == ["checkpoint-inspected:a.ckpt"]


def test_a_missing_positional_is_refused(project, capsys):
    assert build_app([]).dispatch(["inspect", "checkpoint"]) == 1
    assert "takes positionals: checkpoint" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# dispatch and help
# --------------------------------------------------------------------------- #


def test_the_longest_command_path_wins(project):
    record: list[str] = []
    app = build_app(record)
    handler, remainder = app.match(["inspect", "checkpoint", "a.ckpt"])
    assert handler.spec.name == ("inspect", "checkpoint")
    assert remainder == ("a.ckpt",)


def test_an_unknown_command_lists_the_known_ones(project, capsys):
    assert build_app([]).dispatch(["trian"]) == 2
    err = capsys.readouterr().err
    assert "unknown command" in err
    assert "inspect config-compare" not in err
    assert "train" in err


def test_usage_is_generated_from_the_declarations(project, capsys):
    assert build_app([]).dispatch([]) == 0
    out = capsys.readouterr().out
    assert "train" in out and "train a model" in out
    assert "inspect checkpoint" in out


def test_command_help_lists_positionals_sources_and_options(project, capsys):
    assert build_app([]).dispatch(["eval", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--resolved" in out
    options = out.split("options:")[1]
    assert "--config VALUE" not in options  # eval does not accept an authored file
    assert "--clobber" in out


def test_help_for_a_targetless_command_omits_the_destructive_options(project, capsys):
    assert build_app([]).dispatch(["inspect", "checkpoint", "--help"]) == 0
    out = capsys.readouterr().out
    assert "takes no configuration" in out
    assert "--clobber" not in out
    assert "--resume" not in out


def test_an_unknown_option_is_a_refusal_not_a_traceback(project, capsys):
    assert build_app([]).dispatch(["train", "--nope"]) == 1
    err = capsys.readouterr().err
    assert "unknown option" in err
    assert "Traceback" not in err


def test_a_defect_keeps_its_traceback(project):
    """A refusal is a message to the user; anything else is a bug, and swallowing
    it would trade a diagnosable crash for a mystery."""
    app = Application(
        name="mini",
        root_type=Root,
        hashes=HASHES,
        compose_spec=ComposeSpec(packaged=None, local="configs"),
    )

    @app.command(CommandSpec(name=("boom",), target=TARGET, continue_artifact="checkpoint"))
    def boom(run):
        raise ZeroDivisionError("a real bug")

    with pytest.raises(ZeroDivisionError):
        app.dispatch(["boom"])


def test_the_manifest_records_the_invocation_text(project):
    app = build_app([])
    app.dispatch(["train", "epochs=50", "--clobber"])
    run_dir = next((project / "runs" / "reef-v3").iterdir())
    argv = Manifest.load(run_dir).data["invocations"][0]["argv"]
    assert argv == "train --clobber epochs=50"
    assert MANIFEST_NAME in [p.name for p in run_dir.iterdir()]


def test_an_inapplicable_framework_option_is_refused(project):
    """Help and the parser used to disagree: `inspect checkpoint` advertised
    neither --clobber nor --resume, and the parser accepted both in silence."""
    app = build_app([])
    handler = app.handlers["inspect-checkpoint"]
    assert "--clobber" not in app.command_help(handler)
    assert app.dispatch(["inspect", "checkpoint", "a.ckpt", "--clobber"]) == 1


def test_help_offers_exactly_what_the_parser_accepts(project):
    """One derivation feeds both, so they cannot drift apart."""
    app = build_app([])
    for handler in app.handlers.values():
        offered = set(handler.spec.applicable_options())
        text = app.command_help(handler)
        for option in ("--clobber", "--resume", "--config-dir"):
            assert (option in text) == (option in offered), (
                f"{handler.spec.invocation}: help and derivation disagree on {option}"
            )


def test_the_framework_reserves_no_option_governing_how_much_work_runs(project):
    from honbu.flow.compose import FRAMEWORK_OPTIONS

    assert "--init" not in {option.name for option in FRAMEWORK_OPTIONS}
