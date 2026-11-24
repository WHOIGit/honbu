"""A complete Honbu project in one file.

It fits a line to points by gradient descent, in about forty lines of arithmetic
and no dependencies, so that everything around it is visibly Honbu's: the schema,
the identities, the commands, the run.

Every code sample in the documentation is quoted from this file between the
``[start:...]`` markers, so a sample cannot drift from working code — the test
suite runs this project end to end, and a renamed API fails there before it can
mislead a reader.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated

import typer
from pydantic import Field

# --8<-- [start:imports]
from honbu import (
    DIRECTIVE,
    ExperimentConfig,
    FieldHash,
    HashRegistry,
    HashedRuntimeConfig,
    OutputBlock,
    OutputLeaf,
    PortablePath,
    SourceHash,
    StrictModel,
    validate_root_contract,
)
from honbu.cli import (
    emit_warnings,
    exit_on_refusal,
    select_config_source,
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
)
from honbu.flow import (
    CommandSpec,
    ComposeSpec,
    ProjectSpec,
    StepSpec,
    TargetSpec,
    load_config,
    open_run,
    parent_record,
    resolve,
)

# --8<-- [end:imports]


# --8<-- [start:blocks]
class DataConfig(StrictModel):
    """Where the points are. `PortablePath` is the marker that makes this a path.

    A marked path is stored absolute in the resolved configuration and relative in
    the portable one, and it is excluded from every digest — so moving the project
    to another machine does not change what the run *is*.
    """

    points: PortablePath


class TrainingConfig(StrictModel):
    epochs: int = 500
    learning_rate: float = 0.2


class Outputs(OutputBlock):
    """Where the run goes. The template is authored; the directory is resolved."""

    dir_template: Annotated[str | None, DIRECTIVE] = "{experiment.name}/{runtime.run_id}"
    checkpoints: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir="checkpoints"))
    reports: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir="reports"))


class RootConfig(StrictModel):
    """The root stays flat: one namespace for group packages and hash paths alike."""

    experiment: ExperimentConfig
    runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    data: DataConfig
    training: TrainingConfig = Field(default_factory=TrainingConfig)


# Checked at import rather than at the first run: the four requirable blocks are
# present and of the base type or a subclass, and output_root carries PathMarker.
validate_root_contract(RootConfig)
# --8<-- [end:blocks]


# --8<-- [start:hashes]
def read_points(path: Path) -> list[tuple[float, float]]:
    return [
        (float(x), float(y))
        for x, y in (line.split(",") for line in path.read_text().split() if line)
    ]


HASHES = HashRegistry(
    [
        # What the configuration says. Paths, derived values, templates, and
        # directives are excluded by their markers, so this is the *behavioral*
        # configuration.
        FieldHash("config_hash", include=("experiment", "data", "training"), version="1"),
        # What the inputs are. Reading inputs makes it deferred: it cannot be known
        # before the run opens, so the run completes it and the manifest records
        # when.
        SourceHash(
            "dataset_hash",
            build=lambda ctx: {"version": "1", "points": ctx["points"]},
            reads_inputs=True,
        ),
    ],
    root_type=RootConfig,
)
# --8<-- [end:hashes]


# --8<-- [start:commands]
TRAIN = CommandSpec(
    name=("train",),
    target=TargetSpec(
        template="outputs.dir_template",
        resolved="outputs.dir",
        root="output_root",
    ),
    hashes=("config_hash", "dataset_hash"),
    steps=(StepSpec("fit"), StepSpec("evaluate")),
    config_sources=("default", "file", "resolved", "fork"),
    fork_artifact="checkpoint",
)

#: An augmenting command: it points at a run that exists and may replace exactly
#: one output leaf. `--clobber` on the report cannot reach the checkpoints.
REPORT = CommandSpec(
    name=("report",),
    mode="augment",
    target=TRAIN.target,
    scope=("outputs.reports",),
    config_sources=("resolved",),
    advances_run=False,
)

PROJECT = ProjectSpec(
    root_type=RootConfig,
    compose_spec=ComposeSpec(packaged="linefit.config_defaults"),
    hashes=HASHES,
)

APP = typer.Typer(no_args_is_help=True)
# --8<-- [end:commands]


# --8<-- [start:handler]
@APP.command("train", context_settings=HYDRA_CONTEXT_SETTINGS)
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
    """Fit a line to the configured points."""
    with exit_on_refusal(prog="linefit"):
        overrides = validate_override_tokens(ctx.args)
        source, ref = select_config_source(
            config=config,
            resolved=resolved,
            fork_from=fork_from,
            accepted=TRAIN.config_sources,
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
                loaded.prior_run,
                artifact=fork_artifact,
                command=TRAIN,
                project=PROJECT,
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
        argv_text = shlex.join(["train", *overrides])
        with open_run(
            res,
            composed=loaded.composed,
            project=PROJECT,
            argv_text=argv_text,
            clobber=clobber,
            resume=resume,
            parent=parent,
        ) as run:
            emit_warnings(run.warnings)
            points = read_points(run.config.data.points)

            if not run.skip("fit"):
                with run.step("fit"):
                    # A deferred hash is completed where its inputs are first read, which
                    # is the only place that can honestly report what was read.
                    run.complete_hash("dataset_hash", inputs={"points": len(points)})
                    slope, intercept = fit(points, run.config.training)
                    checkpoint = run.config.outputs.checkpoints.dir / "line.json"
                    checkpoint.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint.write_text(json.dumps({"slope": slope, "intercept": intercept}))
                    run.record_artifact("checkpoint", checkpoint)

            with run.step("evaluate"):
                line = json.loads(run.artifact("checkpoint").read_text())
                # The objective. Honbu stores it and reads none of it; a sweep or a
                # comparison reads it back out of the manifest.
                run.record_best(epoch=run.config.training.epochs, metrics=loss_of(points, line))
# --8<-- [end:handler]


# --8<-- [start:augmenting]
@APP.command("report", context_settings=HYDRA_CONTEXT_SETTINGS)
def report(
    ctx: typer.Context,
    resolved: ResolvedOpt = None,
    clobber: ClobberOpt = False,
) -> None:
    """Write a report beside an existing run."""
    with exit_on_refusal(prog="linefit"):
        overrides = validate_override_tokens(ctx.args)
        source, ref = select_config_source(resolved=resolved, accepted=REPORT.config_sources)
        loaded = load_config(
            source,
            ref,
            overrides=overrides,
            project=PROJECT,
            command=REPORT,
        )
        res = resolve(
            loaded.composed,
            command=REPORT,
            project=PROJECT,
            existing_target=loaded.prior_run,
            replay=loaded.replay,
            overrides=loaded.overrides,
        )
        with open_run(
            res,
            composed=loaded.composed,
            project=PROJECT,
            argv_text=shlex.join(["report", *overrides]),
            clobber=clobber,
        ) as run:
            emit_warnings(run.warnings)
            line = json.loads(run.artifact("checkpoint").read_text())
            destination = run.config.outputs.reports.dir
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "line.txt").write_text(
                f"y = {line['slope']:.4f}x + {line['intercept']:.4f}\n"
            )
# --8<-- [end:augmenting]


# --8<-- [start:main]
def main() -> None:
    APP()
# --8<-- [end:main]


# --- the arithmetic, which is not the point ---------------------------------


def fit(points: list[tuple[float, float]], training: TrainingConfig) -> tuple[float, float]:
    """Gradient descent on the mean squared error. Deterministic, no dependencies."""
    slope, intercept = 0.0, 0.0
    for _ in range(training.epochs):
        d_slope = sum(2 * x * (slope * x + intercept - y) for x, y in points) / len(points)
        d_intercept = sum(2 * (slope * x + intercept - y) for x, y in points) / len(points)
        slope -= training.learning_rate * d_slope
        intercept -= training.learning_rate * d_intercept
    return slope, intercept


def loss_of(points: list[tuple[float, float]], line: dict) -> dict[str, float]:
    errors = [(line["slope"] * x + line["intercept"] - y) ** 2 for x, y in points]
    return {"mse": sum(errors) / len(errors)}


if __name__ == "__main__":  # pragma: no cover - console entry point
    main()
