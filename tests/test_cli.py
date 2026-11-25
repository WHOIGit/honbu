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
    resolve_positional_config,
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
    config_option,
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
# forgiving positional [CONFIG] resolution
# --------------------------------------------------------------------------- #


def _positional_tree(tmp_path, monkeypatch, pkg_name):
    pkg = tmp_path / pkg_name
    (pkg / "studies").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.yaml").write_text("x: 1\n")
    (pkg / "configA.yaml").write_text("x: packaged\n")
    (pkg / "studies" / "v1_baseline.yaml").write_text("x: study\n")
    (tmp_path / "configs").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return pkg, ComposeSpec(packaged=pkg_name, local="configs")


def test_positional_config_resolves_search_path_and_suffix_forms(tmp_path, monkeypatch):
    pkg, spec = _positional_tree(tmp_path, monkeypatch, "pos_pkg")
    for token in ("configA", "configA.yaml"):
        located = resolve_positional_config(token, compose_spec=spec)
        assert located == (pkg / "configA.yaml").resolve()
        assert load_config("file", located, compose_spec=spec).composed == {"x": "packaged"}
    assert resolve_positional_config("studies/v1_baseline", compose_spec=spec) == (
        pkg / "studies" / "v1_baseline.yaml"
    ).resolve()

    # A direct filesystem path works with or without a spec, absolute or relative.
    (tmp_path / "exp").mkdir()
    (tmp_path / "exp" / "mine.yaml").write_text("x: 2\n")
    located = resolve_positional_config("exp/mine")
    assert located == (tmp_path / "exp" / "mine.yaml").resolve()
    assert load_config("file", located).composed == {"x": 2}
    absolute = (tmp_path / "exp" / "mine.yaml").resolve()
    assert resolve_positional_config(str(absolute), compose_spec=spec) == absolute

    # .yml is refused cleanly because Hydra composes .yaml only.
    (tmp_path / "exp" / "legacy.yml").write_text("x: 3\n")
    with pytest.raises(RefusalError, match=r"\.yaml"):
        resolve_positional_config("exp/legacy.yml")
    with pytest.raises(RefusalError, match="no configuration named 'exp/legacy'"):
        resolve_positional_config("exp/legacy")


def test_positional_config_explicit_fs_paths_skip_search_path(tmp_path, monkeypatch):
    _, spec = _positional_tree(tmp_path, monkeypatch, "trav_pkg")
    # .. must never escape ./configs/ to accidentally match a file in cwd.
    (tmp_path / "cwd_only.yaml").write_text("x: cwd\n")
    with pytest.raises(RefusalError, match="no configuration named"):
        resolve_positional_config("../cwd_only.yaml", compose_spec=spec)

    # ./ explicitly anchors to cwd without colliding with ./configs/configA.yaml.
    (tmp_path / "configA.yaml").write_text("x: cwd\n")
    (tmp_path / "configs" / "configA.yaml").write_text("x: local\n")
    assert resolve_positional_config("./configA.yaml", compose_spec=spec) == (
        tmp_path / "configA.yaml"
    ).resolve()
    assert resolve_positional_config("./configA", compose_spec=spec) == (
        tmp_path / "configA.yaml"
    ).resolve()


def test_positional_config_local_shadow_is_one_match(tmp_path, monkeypatch):
    _, spec = _positional_tree(tmp_path, monkeypatch, "shadow_pos_pkg")
    local = tmp_path / "configs" / "configA.yaml"
    local.write_text("x: local\n")
    assert resolve_positional_config("configA", compose_spec=spec) == local.resolve()

    # Naming the shadow directly is the same file, so still one match.
    assert resolve_positional_config("configs/configA", compose_spec=spec) == local.resolve()

    # --config-dir overlays take precedence over local and packaged.
    (tmp_path / "overlay").mkdir()
    (tmp_path / "overlay" / "configA.yaml").write_text("x: overlay\n")
    assert resolve_positional_config(
        "configA", compose_spec=spec, extra_dirs=["overlay"]
    ) == (tmp_path / "overlay" / "configA.yaml").resolve()

    # ProjectSpec and an explicit cwd work too.
    monkeypatch.chdir("/")
    project = ProjectSpec(root_type=Root, compose_spec=spec, hashes=HASHES)
    assert resolve_positional_config("configA", project=project, cwd=tmp_path) == (
        local.resolve()
    )


def test_positional_config_refuses_ambiguity_listing_candidates(tmp_path, monkeypatch):
    pkg, spec = _positional_tree(tmp_path, monkeypatch, "ambig_pkg")
    (tmp_path / "configA.yaml").write_text("x: cwd\n")
    with pytest.raises(RefusalError, match="ambiguous") as info:
        resolve_positional_config("configA", compose_spec=spec)
    message = str(info.value)
    assert str((tmp_path / "configA.yaml").resolve()) in message
    assert str((pkg / "configA.yaml").resolve()) in message
    assert "-c/--config" in message

    # Nested configs/configs/configA.yaml collides with ./configs/configA.yaml.
    (tmp_path / "configs" / "configs").mkdir(parents=True)
    (tmp_path / "configs" / "configA.yaml").write_text("x: a\n")
    (tmp_path / "configs" / "configs" / "configA.yaml").write_text("x: b\n")
    with pytest.raises(RefusalError, match="ambiguous"):
        resolve_positional_config("configs/configA", compose_spec=spec)


def test_positional_config_refuses_no_match_with_override_hint(tmp_path, monkeypatch):
    _, spec = _positional_tree(tmp_path, monkeypatch, "none_pkg")
    with pytest.raises(RefusalError, match="no configuration named 'epochs'") as info:
        resolve_positional_config("epochs", compose_spec=spec)
    assert "key=value" in str(info.value)
    assert "--" in str(info.value)
    with pytest.raises(RefusalError, match="empty"):
        resolve_positional_config("  ", compose_spec=spec)
    # A plain directory is not a configuration file.
    (tmp_path / "studies").mkdir()
    with pytest.raises(RefusalError, match="no configuration named"):
        resolve_positional_config("studies", compose_spec=ComposeSpec(packaged=None))


@pytest.mark.parametrize("marker", ["manifest.json", "config/resolved.yaml"])
def test_positional_config_refuses_run_directories(tmp_path, monkeypatch, marker):
    _, spec = _positional_tree(tmp_path, monkeypatch, f"rundir_pkg_{marker[0]}")
    run = tmp_path / "runs" / "r1"
    (run / marker).parent.mkdir(parents=True, exist_ok=True)
    (run / marker).write_text("{}\n")
    with pytest.raises(RefusalError, match="run directory") as info:
        resolve_positional_config("runs/r1", compose_spec=spec)
    assert "--resolved" in str(info.value) and "--fork-from" in str(info.value)
    # Opting out of the guardrail leaves the caller to handle run dirs: a
    # directory still is not an authored file.
    with pytest.raises(RefusalError, match="no configuration named"):
        resolve_positional_config("runs/r1", compose_spec=spec, reject_run_dir=False)


def test_config_option_exposes_short_and_long_flags():
    # Under Annotated, Typer reads the leading positional as the first declaration.
    def decls(info):
        return (info.default, *info.param_decls)

    assert decls(config_option()) == ("-c", "--config")
    assert decls(config_option("--cfg")) == ("--cfg",)

    app = typer.Typer()
    seen: dict[str, Path | None] = {}

    @app.command()
    def show(config: ConfigOpt = None) -> None:
        seen["config"] = config

    assert RUNNER.invoke(app, ["-c", "a.yaml"]).exit_code == 0
    assert seen["config"] == Path("a.yaml")
    assert RUNNER.invoke(app, ["--config", "b.yaml"]).exit_code == 0
    assert seen["config"] == Path("b.yaml")


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


# --------------------------------------------------------------------------- #
# N-way compare command: @default, @packaged, @packaged:<name>, forgiving names
# --------------------------------------------------------------------------- #


@pytest.fixture
def compare_app(tmp_path, monkeypatch):
    pkg = tmp_path / "cmp_cli_pkg"
    (pkg / "data").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.yaml").write_text("defaults:\n  - _self_\n  - data: a\nepochs: 10\n")
    (pkg / "configA.yaml").write_text("defaults:\n  - _self_\n  - data: b\nepochs: 20\n")
    (pkg / "data" / "a.yaml").write_text("uri: ./a\n")
    (pkg / "data" / "b.yaml").write_text("uri: ./b\n")
    local = tmp_path / "configs"
    local.mkdir()
    (local / "config.yaml").write_text("defaults:\n  - _self_\n  - data: a\nepochs: 11\n")
    (local / "default.yaml").write_text("epochs: 99\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    app = typer.Typer()
    app.command("compare", context_settings=HYDRA_CONTEXT_SETTINGS)(
        build_compare_command(
            compose_spec=ComposeSpec(packaged="cmp_cli_pkg", local="configs"),
            default_level="literal",
            prog="mini",
        )
    )
    # A second command so Typer keeps "compare" as a subcommand name.
    app.command("noop")(lambda: None)
    return app


def test_compare_command_designators_and_n_way(compare_app):
    from honbu.flow import CLI_TXT_NAME

    assert CLI_TXT_NAME == "cli.txt"
    out = RUNNER.invoke(compare_app, ["compare", "@packaged", "@default", "@packaged:configA"])
    assert out.exit_code == 0, out.output
    assert "epochs: @packaged=10, @default=11, @packaged:configA=20" in out.output
    assert "data.uri" in out.output and "'./b'" in out.output

    # @packaged:<name>.yaml strips the redundant suffix cleanly.
    with_ext = RUNNER.invoke(
        compare_app, ["compare", "@packaged:configA", "@packaged:configA.yaml"]
    )
    assert with_ext.exit_code == 0, with_ext.output
    assert "No literal differences" in with_ext.output


def test_compare_command_forgiving_names_do_not_collide_with_designators(compare_app):
    # A local file named default.yaml is a config name, never @default.
    out = RUNNER.invoke(compare_app, ["compare", "default", "@default"])
    assert out.exit_code == 0, out.output
    assert "epochs: default=99, @default=11" in out.output

    # configA resolves from packaged defaults through the search path.
    named = RUNNER.invoke(compare_app, ["compare", "configA", "@packaged:configA"])
    assert named.exit_code == 0, named.output
    assert "No literal differences" in named.output


def test_compare_command_overrides_apply_to_the_operand_they_follow(compare_app):
    out = RUNNER.invoke(
        compare_app,
        ["compare", "@default", "epochs=1", "@default", "@default", "epochs=3"],
    )
    assert out.exit_code == 0, out.output
    assert "epochs: @default=1, @default=11, @default=3" in out.output


def test_compare_command_config_dir_and_run_dirs(compare_app, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "configA.yaml").write_text("epochs: 7\n")
    out = RUNNER.invoke(
        compare_app, ["compare", "configA", "@packaged:configA", "--config-dir", str(overlay)]
    )
    assert out.exit_code == 0, out.output
    assert "epochs: configA=7, @packaged:configA=20" in out.output

    run = tmp_path / "runs" / "r1"
    (run / "config").mkdir(parents=True)
    (run / "manifest.json").write_text("{}\n")
    (run / "config" / "resolved.yaml").write_text("epochs: 5\n")
    (run / "config" / "composed.yaml").write_text("epochs: 6\n")
    resolved = RUNNER.invoke(compare_app, ["compare", "runs/r1", "@default"])
    assert resolved.exit_code == 0, resolved.output
    assert "epochs: runs/r1=5, @default=11" in resolved.output
    composed = RUNNER.invoke(compare_app, ["compare", "--composed", "runs/r1", "@default"])
    assert composed.exit_code == 0, composed.output
    assert "runs/r1=6" in composed.output


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["@default"], "at least two operands"),
        (["epochs=1", "@default", "@packaged"], "precedes every operand"),
        (["@default", "@nope"], "unknown operand designator"),
        (["@default", "@packaged:"], "names no packaged root"),
        (["@default", "packaged"], "Did you mean @packaged?"),
        (["@default", "missing"], "no configuration named 'missing'"),
        (["@default", "@packaged", "--nope"], "unknown option '--nope'"),
        (["@default", "@packaged", "epochs=1,2"], "multi-value sweep"),
    ],
)
def test_compare_command_refusals(compare_app, argv, message):
    out = RUNNER.invoke(compare_app, ["compare", *argv])
    assert out.exit_code == 1, out.output
    assert message in out.output
    assert "Traceback" not in out.output


def test_compare_command_refuses_ambiguous_names(compare_app, tmp_path):
    (tmp_path / "configA.yaml").write_text("epochs: 1\n")
    out = RUNNER.invoke(compare_app, ["compare", "configA", "@default"])
    assert out.exit_code == 1
    assert "ambiguous" in out.output and "-c/--config" in out.output
