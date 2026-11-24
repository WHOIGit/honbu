"""Opt-in CLI patterns: library-agnostic honbu.cli and Typer honbu.cli.presets (D19)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Annotated

import pytest
import typer
from pydantic import Field
from typer.testing import CliRunner

from honbu import (
    DIRECTIVE,
    ExperimentConfig,
    FieldHash,
    HashRegistry,
    HashedRuntimeConfig,
    OutputBlock,
    OutputLeaf,
    PortablePath,
    RefusalError,
    SourceHash,
    StrictModel,
)
from honbu.cli import (
    CONVENTIONAL_FLAGS,
    emit_warnings,
    exit_on_refusal,
    select_config_source,
    split_dash_rule,
    validate_override_tokens,
)
from honbu.cli.presets import (
    HYDRA_CONTEXT_SETTINGS,
    ClobberOpt,
    ConfigDirsOpt,
    ConfigOpt,
    ForkArtifactOpt,
    ForkFromOpt,
    ResolvedOpt,
    ResumeOpt,
    build_compare_command,
    resolved_option,
)
from honbu.flow import (
    CommandSpec,
    ComposeSpec,
    Manifest,
    ProjectSpec,
    StepSpec,
    TargetSpec,
    load_config,
    open_run,
    parent_record,
    resolve,
)

RUNNER = CliRunner()


# --------------------------------------------------------------------------- #
# library-agnostic honbu.cli helpers
# --------------------------------------------------------------------------- #


def test_split_dash_rule_routes_overrides_positionals_and_refuses_unknown_flags():
    split = split_dash_rule(["experiment=reef", "a.ckpt", "+new.key=1", "++f=2", "~drop.key"])
    assert split.overrides == ("experiment=reef", "+new.key=1", "++f=2", "~drop.key")
    assert split.positionals == ("a.ckpt",)

    with pytest.raises(RefusalError, match="unknown option '--nope'"):
        split_dash_rule(["--nope"])


def test_validate_override_tokens_catches_flags_bare_args_commas_and_braces():
    assert validate_override_tokens(["epochs=10", "+extra=1", "~sweep.axes"]) == (
        "epochs=10",
        "+extra=1",
        "~sweep.axes",
    )
    with pytest.raises(RefusalError, match="unknown option '--bad'"):
        validate_override_tokens(["--bad"])
    with pytest.raises(RefusalError, match="unexpected argument"):
        validate_override_tokens(["bare-token"])
    with pytest.raises(RefusalError, match="multi-value sweep"):
        validate_override_tokens(["epochs=10,20"])
    with pytest.raises(RefusalError, match="Honbu template"):
        validate_override_tokens(["outputs.dir_template={experiment.name}"])


def test_select_config_source_is_flag_independent_and_enforces_exclusivity():
    all_sources = ("default", "file", "resolved", "fork")
    assert select_config_source(accepted=all_sources) == ("default", None)
    assert select_config_source(config="a.yaml", accepted=all_sources) == ("file", "a.yaml")
    assert select_config_source(resolved="runs/x", accepted=all_sources) == ("resolved", "runs/x")
    assert select_config_source(fork_from="runs/x", accepted=all_sources) == ("fork", "runs/x")

    with pytest.raises(RefusalError, match="mutually exclusive.*--config.*--resolved"):
        select_config_source(config="a.yaml", resolved="runs/x", accepted=all_sources)

    # Custom flag spellings in refusal messages
    with pytest.raises(RefusalError, match="--resolved-config"):
        select_config_source(
            accepted=("resolved",),
            flag_names={"resolved": "--resolved-config"},
        )
    with pytest.raises(RefusalError, match="takes no configuration"):
        select_config_source(config="a.yaml", accepted=())
    assert select_config_source(accepted=()) == (None, None)


def test_exit_on_refusal_prints_cleanly_keeps_defects_and_supports_structured_hook():
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        with exit_on_refusal(prog="mini", stream=buf):
            raise RefusalError("bad input")
    assert exc.value.code == 1
    assert buf.getvalue() == "mini: bad input\n"

    # Structured error hook (e.g., --format json)
    captured: list[str] = []
    with pytest.raises(SystemExit) as exc2:
        with exit_on_refusal(on_error=lambda err: captured.append(json.dumps({"error": str(err)}))):
            raise RefusalError("structured")
    assert exc2.value.code == 1
    assert captured == ['{"error": "structured"}']

    # Defects propagate intact with traceback
    with pytest.raises(ZeroDivisionError):
        with exit_on_refusal(prog="mini"):
            1 / 0

    # KeyboardInterrupt exits 130
    buf2 = io.StringIO()
    with pytest.raises(SystemExit) as kbd:
        with exit_on_refusal(prog="mini", stream=buf2):
            raise KeyboardInterrupt
    assert kbd.value.code == 130
    assert "interrupted" in buf2.getvalue()


def test_emit_warnings_and_conventional_flags():
    buf = io.StringIO()
    emit_warnings(("first", "second"), stream=buf)
    assert buf.getvalue() == "warning: first\nwarning: second\n"
    assert CONVENTIONAL_FLAGS["fork_from"] == "--fork-from"
    assert CONVENTIONAL_FLAGS["fork_artifact"] == "--fork-artifact"


# --------------------------------------------------------------------------- #
# Typer presets end-to-end project
# --------------------------------------------------------------------------- #


class Data(StrictModel):
    uri: PortablePath


class TrainingOutputs(OutputBlock):
    dir_template: Annotated[str | None, DIRECTIVE] = "{experiment.name:slug}/{runtime.config_id}"
    checkpoints: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir=Path("checkpoints")))
    figures: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir=Path("figures")))


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
    data: Data
    epochs: int = 10
    output_root: PortablePath = Path("./runs")
    training_outputs: TrainingOutputs = Field(default_factory=TrainingOutputs)


TARGET = TargetSpec("training_outputs.dir_template", "training_outputs.dir", "output_root")
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
PROJECT = ProjectSpec(
    root_type=Root,
    compose_spec=ComposeSpec(packaged=None, local="configs"),
    hashes=HASHES,
)

TRAIN = CommandSpec(
    name=("train",),
    target=TARGET,
    hashes=("config_hash", "dataset_hash"),
    fork_artifact="checkpoint",
    steps=(
        StepSpec("prepare", on_resume="restart"),
        StepSpec("fit", scope=("training_outputs.checkpoints",)),
    ),
)
EVAL = CommandSpec(
    name=("eval",),
    mode="augment",
    target=TARGET,
    config_sources=("resolved",),
    scope=("training_outputs.figures",),
    hashes=("config_hash",),
)


def _build_typer_app() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)

    @app.command("train", context_settings=HYDRA_CONTEXT_SETTINGS)
    def train(
        ctx: typer.Context,
        config: ConfigOpt = None,
        resolved: ResolvedOpt = None,
        fork_from: ForkFromOpt = None,
        fork_artifact: ForkArtifactOpt = None,
        config_dir: ConfigDirsOpt = None,
        clobber: ClobberOpt = False,
        resume: ResumeOpt = False,
    ) -> None:
        with exit_on_refusal(prog="mini"):
            overrides = validate_override_tokens(ctx.args)
            source, ref = select_config_source(
                config=config, resolved=resolved, fork_from=fork_from, accepted=TRAIN.config_sources
            )
            loaded = load_config(
                source,
                ref,
                overrides=overrides,
                extra_dirs=config_dir or (),
                project=PROJECT,
                command=TRAIN,
            )
            parent = (
                parent_record(
                    loaded.prior_run, artifact=fork_artifact, command=TRAIN, project=PROJECT
                )
                if loaded.source == "fork" and loaded.prior_run is not None
                else None
            )
            res = resolve(
                loaded.composed,
                command=TRAIN,
                project=PROJECT,
                replay=loaded.replay,
                overrides=loaded.overrides,
            )
            with open_run(
                res,
                composed=loaded.composed,
                project=PROJECT,
                argv_text=" ".join(["train", *overrides]),
                clobber=clobber,
                resume=resume,
                parent=parent,
            ) as run:
                for step in ("prepare", "fit"):
                    if run.skip(step):
                        continue
                    with run.step(step):
                        if step == "prepare":
                            run.complete_hash("dataset_hash", inputs={"rows": 42})
                        else:
                            ckpt = run.config.training_outputs.checkpoints.dir
                            ckpt.mkdir(parents=True, exist_ok=True)
                            (ckpt / "last.pt").write_text("weights")
                            run.record_artifact("checkpoint", ckpt / "last.pt")
                            run.record_best(epoch=7, metrics={"map50": 0.6})

    @app.command("eval", context_settings=HYDRA_CONTEXT_SETTINGS)
    def evaluate(
        ctx: typer.Context,
        # Demonstrate customizable option factory renaming --resolved to --run
        run_ref: Annotated[Path | None, resolved_option("--run", help="Prior run dir.")] = None,
        clobber: ClobberOpt = False,
    ) -> None:
        with exit_on_refusal(prog="mini"):
            overrides = validate_override_tokens(ctx.args)
            source, ref = select_config_source(
                resolved=run_ref,
                accepted=EVAL.config_sources,
                flag_names={"resolved": "--run"},
            )
            loaded = load_config(
                source, ref, overrides=overrides, project=PROJECT, command=EVAL
            )
            res = resolve(
                loaded.composed,
                command=EVAL,
                project=PROJECT,
                existing_target=loaded.prior_run,
                replay=loaded.replay,
                overrides=loaded.overrides,
            )
            with open_run(res, composed=loaded.composed, project=PROJECT, clobber=clobber) as run:
                fig = run.config.training_outputs.figures.dir
                fig.mkdir(parents=True, exist_ok=True)
                (fig / "pr.png").write_text("plot")

    app.command("compare", context_settings=HYDRA_CONTEXT_SETTINGS)(
        build_compare_command(project=PROJECT)
    )
    return app


@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "config.yaml").write_text(
        "experiment:\n  name: reef v3\ndata:\n  uri: ./datasets/coco8\nepochs: 10\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_typer_presets_train_fork_eval_compare_and_refusals(mini_project):
    app = _build_typer_app()

    # 1. Train default
    res = RUNNER.invoke(app, ["train"])
    assert res.exit_code == 0, res.output
    (first_run,) = (mini_project / "runs" / "reef-v3").iterdir()
    assert Manifest.load(first_run).state == "completed"

    # 2. Collision refuses cleanly with exit code 1 and no traceback
    dup = RUNNER.invoke(app, ["train"])
    assert dup.exit_code == 1
    assert "not empty" in dup.output
    assert "Traceback" not in dup.output

    # 3. Unknown flag caught cleanly
    bad = RUNNER.invoke(app, ["train", "--nope"])
    assert bad.exit_code == 1
    assert "unknown option '--nope'" in bad.output

    # 4. Fork creates a new run with recorded lineage
    forked = RUNNER.invoke(app, ["train", "--fork-from", str(first_run), "epochs=50"])
    assert forked.exit_code == 0, forked.output
    runs = sorted((mini_project / "runs" / "reef-v3").iterdir())
    assert len(runs) == 2
    child = next(r for r in runs if r != first_run)
    lineage = Manifest.load(child).data["parent"]
    assert lineage["run_dir"] == str(first_run)
    assert lineage["artifact_key"] == "checkpoint"
    assert lineage["artifact"] == "checkpoints/last.pt"

    # 5. Custom-renamed --run flag works on eval and uses custom name in refusal
    missing_eval = RUNNER.invoke(app, ["eval"])
    assert missing_eval.exit_code == 1
    assert "--run" in missing_eval.output

    ok_eval = RUNNER.invoke(app, ["eval", "--run", str(first_run)])
    assert ok_eval.exit_code == 0, ok_eval.output
    assert (first_run / "figures" / "pr.png").is_file()

    # 6. Mountable compare command builder
    cmp = RUNNER.invoke(app, ["compare", str(first_run), str(child)])
    assert cmp.exit_code == 0, cmp.output
    assert "epochs" in cmp.output
