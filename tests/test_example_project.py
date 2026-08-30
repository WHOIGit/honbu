"""The documented project, run the way the documentation says to run it.

Every code sample in `docs/` is quoted from `examples/linefit/project.py` between
snippet markers, so this file is what keeps the documentation true: rename an API
and this fails, rather than a reader finding out.

The assertions are deliberately about the *documented claims* — that a run records
its identities, that a completed step is not repeated, that an augmenting command
cannot reach the checkpoints — and not about the arithmetic, which is not the point
of the example.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honbu.core.targets import MANIFEST_NAME
from honbu.flow import Manifest

from linefit.project import APP, HASHES, RootConfig


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The example, invoked from a working directory of its own.

    No local `configs/` is written: the project composes from its packaged
    defaults, which is the arrangement the configuration guide describes.
    """
    points = tmp_path / "data" / "points.csv"
    points.parent.mkdir(parents=True)
    points.write_text("\n".join(f"{x / 10},{2 * (x / 10) + 1}" for x in range(10)) + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def only_run(project: Path) -> Path:
    (run_dir,) = (project / "runs" / "linefit").iterdir()
    return run_dir


# --- the tutorial's path -----------------------------------------------------


def test_the_documented_first_run_completes(project):
    assert APP.dispatch(["train"], cwd=project) == 0

    manifest = Manifest.load(only_run(project))
    assert manifest.state == "completed"
    assert manifest.data["best"]["metrics"]["mse"] == pytest.approx(0.0, abs=1e-3)


def test_a_run_records_what_produced_it(project):
    """The claim the identity page makes: the manifest says what this run was."""
    APP.dispatch(["train"], cwd=project)

    data = json.loads((only_run(project) / MANIFEST_NAME).read_text())
    assert set(data["hashes"]) == {"config_hash", "dataset_hash"}
    assert data["invocations"][0]["argv"] == "train"
    assert "checkpoint" in data["artifacts"]


def test_the_configuration_bundle_is_written_beside_the_run(project):
    APP.dispatch(["train"], cwd=project)

    names = {path.name for path in (only_run(project) / "config").iterdir()}
    assert {"composed.yaml", "resolved.yaml"} <= names


def test_two_invocations_of_one_configuration_are_two_runs(project):
    """`{runtime.run_id}` is minted per invocation, so an ordinary repeat coexists
    rather than colliding — the motivation for the identity page."""
    APP.dispatch(["train"], cwd=project)
    APP.dispatch(["train"], cwd=project)

    assert len(list((project / "runs" / "linefit").iterdir())) == 2


# --- the claims the how-to pages make ----------------------------------------


def test_an_override_reaches_the_configuration(project):
    APP.dispatch(["train", "training.epochs=3"], cwd=project)

    resolved = (only_run(project) / "config" / "resolved.yaml").read_text()
    assert "epochs: 3" in resolved


def test_a_deferred_hash_is_completed_by_the_run(project):
    """`dataset_hash` reads inputs, so it cannot be known before the run opens."""
    assert "dataset_hash" in HASHES.deferred_names

    APP.dispatch(["train"], cwd=project)

    recorded = json.loads((only_run(project) / MANIFEST_NAME).read_text())["hashes"]
    assert recorded["dataset_hash"]["value"]


def test_a_completed_step_is_not_repeated_on_resume(project, monkeypatch):
    """What `run.skip` buys, shown where it matters: a run that died after fitting.

    The second invocation reads the checkpoint the first one wrote rather than
    fitting again, which is the whole reason a long step is worth declaring.
    """
    from linefit import project as example

    def explode(*_args, **_kwargs):
        raise RuntimeError("evaluation died")

    monkeypatch.setattr(example, "loss_of", explode)
    with pytest.raises(RuntimeError):
        APP.dispatch(["train"], cwd=project)

    run_dir = only_run(project)
    checkpoint = run_dir / "checkpoints" / "line.json"
    stamp = checkpoint.stat().st_mtime_ns
    assert Manifest.load(run_dir).state == "failed"

    monkeypatch.undo()
    assert APP.dispatch(["train", "--resolved", str(run_dir), "--resume"], cwd=project) == 0

    assert checkpoint.stat().st_mtime_ns == stamp
    assert Manifest.load(run_dir).state == "completed"


def test_an_augmenting_command_extends_the_run_it_names(project):
    APP.dispatch(["train"], cwd=project)
    run_dir = only_run(project)

    assert APP.dispatch(["report", "--resolved", str(run_dir)], cwd=project) == 0

    assert (run_dir / "reports" / "line.txt").is_file()
    assert (run_dir / "checkpoints" / "line.json").is_file()
    assert len(Manifest.load(run_dir).data["invocations"]) == 2


def test_an_authored_derived_field_is_refused(project, capsys):
    """`DERIVED` is not documentation: authoring the value Honbu writes is refused."""
    code = APP.dispatch(["train", "runtime.config_hash=pretend"], cwd=project)

    assert code == 1
    assert "Traceback" not in capsys.readouterr().err


def test_the_example_is_a_flat_root_of_declared_blocks():
    """The shape the configuration guide describes, asserted rather than asserted at."""
    assert {"experiment", "runtime", "output_root", "outputs", "data", "training"} == set(
        RootConfig.model_fields
    )
