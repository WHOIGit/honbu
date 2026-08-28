"""A sweep's state: derived from its children, cached for reading, never trusted."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from honbu import RefusalError
from honbu.sweeps import (
    MISSING,
    SWEEP_MANIFEST_NAME,
    child_argv,
    execute_sweep,
    read_progress,
    refresh_sweep,
    scan_sweep,
)
from honbu.flow import Manifest

from tests.test_prepare import prepare, swept


def sweep_dir_of(tmp_path) -> Path:
    return tmp_path / "sweeps" / "one"


def finish(run_dir: Path, *, acc: float = 0.9) -> None:
    """Take a child all the way through, as a real invocation would."""
    manifest = Manifest.load(run_dir)
    manifest.record_lifecycle_event("started")
    manifest.record_best(metrics={"accuracy": acc})
    manifest.record_outcome()


def interrupt(run_dir: Path) -> None:
    manifest = Manifest.load(run_dir)
    manifest.record_lifecycle_event("started")
    manifest.record_lifecycle_event("interrupted")


def kill(run_dir: Path) -> None:
    """A scheduler timeout: the child is left recorded as running."""
    Manifest.load(run_dir).record_lifecycle_event("started")


# --- the state is the children's -------------------------------------------


def test_a_fresh_sweep_is_entirely_outstanding(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    status = scan_sweep(sweep_dir_of(tmp_path))
    assert len(status) == 3
    assert len(status.outstanding) == 3
    assert status.counts() == {"initialized": 3}


def test_progress_follows_the_children_with_nothing_else_written(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    finish(result.children[0].run_dir)
    interrupt(result.children[1].run_dir)

    status = scan_sweep(sweep_dir_of(tmp_path))
    assert status.counts() == {"completed": 1, "interrupted": 1, "initialized": 1}
    assert len(status.completed) == 1
    assert len(status.outstanding) == 2


def test_a_child_killed_by_a_scheduler_is_resumable(tmp_path):
    """SIGKILL does not raise, so the child is left recorded as running. Treating
    that as resumable is what covers a scheduler timeout."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    kill(result.children[0].run_dir)

    status = scan_sweep(sweep_dir_of(tmp_path))
    child = status.children[0]
    assert child.state == "running" and child.resumable


def test_a_child_best_result_is_visible_without_opening_the_run(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    finish(result.children[1].run_dir, acc=0.77)
    status = scan_sweep(sweep_dir_of(tmp_path))
    assert status.children[1].best["metrics"] == {"accuracy": 0.77}


# --- a missing child is a finding, not an omission ---------------------------


def test_a_deleted_child_is_reported_missing_rather_than_dropped(tmp_path):
    """Scanning directories would find the survivors and report a smaller sweep
    that looked complete."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    finish(result.children[0].run_dir)
    finish(result.children[1].run_dir)
    shutil.rmtree(result.children[2].run_dir)

    status = scan_sweep(sweep_dir_of(tmp_path))
    assert len(status) == 3
    assert len(status.missing) == 1
    assert status.missing[0].state == MISSING
    assert len(status.completed) == 2


def test_the_summary_says_when_a_child_is_missing(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    shutil.rmtree(result.children[0].run_dir)
    assert "1 missing" in scan_sweep(sweep_dir_of(tmp_path)).summary()


def test_retired_children_are_excluded_from_counts_but_not_from_the_record(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)

    status = scan_sweep(sweep_dir_of(tmp_path))
    assert len(status) == 3
    assert len(status.current) == 2
    assert len(status.retired) == 1
    assert sum(status.counts().values()) == 2


# --- the cache is a cache ----------------------------------------------------


def test_refreshing_writes_a_readable_cache_beside_the_index(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    finish(result.children[0].run_dir)
    refresh_sweep(sweep_dir_of(tmp_path))

    cached = json.loads((sweep_dir_of(tmp_path) / SWEEP_MANIFEST_NAME).read_text())
    assert cached["counts"] == {"completed": 1, "initialized": 1}
    assert cached["refreshed"]


def test_the_cache_records_when_it_was_taken(tmp_path):
    """Without the stamp the file states a count as though it were current, and it
    is stale exactly when it matters most."""
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    status = refresh_sweep(sweep_dir_of(tmp_path))
    assert read_progress(sweep_dir_of(tmp_path))["refreshed"] == status.refreshed


def test_a_child_run_outside_the_driver_leaves_the_cache_stale(tmp_path):
    """The hazard, made visible rather than papered over. A scheduler updates the
    child's manifest and nothing else."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    refresh_sweep(sweep_dir_of(tmp_path))

    finish(result.children[0].run_dir)  # as an external job would

    assert read_progress(sweep_dir_of(tmp_path))["counts"] == {"initialized": 2}
    assert refresh_sweep(sweep_dir_of(tmp_path)).counts() == {
        "completed": 1,
        "initialized": 1,
    }


def test_deleting_the_cache_changes_nothing(tmp_path):
    """The property that makes the cache safe to have at all."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    finish(result.children[0].run_dir)
    before = refresh_sweep(sweep_dir_of(tmp_path))

    (sweep_dir_of(tmp_path) / SWEEP_MANIFEST_NAME).unlink()
    assert read_progress(sweep_dir_of(tmp_path)) is None

    after = scan_sweep(sweep_dir_of(tmp_path))
    assert after.counts() == before.counts()
    assert [c.state for c in after.children] == [c.state for c in before.children]


def test_a_corrupt_cache_changes_nothing_either(tmp_path):
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    finish(result.children[0].run_dir)
    (sweep_dir_of(tmp_path) / SWEEP_MANIFEST_NAME).write_text('{"counts": {"completed": 99}}')

    assert scan_sweep(sweep_dir_of(tmp_path)).counts() == {
        "completed": 1,
        "initialized": 1,
    }


def test_scanning_a_directory_with_no_index_refuses(tmp_path):
    with pytest.raises(RefusalError, match="no sweep index"):
        scan_sweep(tmp_path / "nowhere")


# --- execution ---------------------------------------------------------------


def test_every_child_is_reached_by_one_uniform_call(tmp_path):
    """A child that never started and one that died at epoch forty are the same
    invocation. That is why the driver can be a for loop."""
    result = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    interrupt(result.children[1].run_dir)
    status = scan_sweep(sweep_dir_of(tmp_path))

    never, died = status.children
    assert child_argv(never) == ["--resolved", str(never.run_dir), "--resume"]
    assert child_argv(died) == ["--resolved", str(died.run_dir), "--resume"]


def test_execution_runs_every_outstanding_child(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    seen = []

    def run(child):
        seen.append(child.index)
        finish(child.run_dir)

    result = execute_sweep(sweep_dir_of(tmp_path), run)
    assert seen == [0, 1, 2]
    assert len(result.ran) == 3
    assert refresh_sweep(sweep_dir_of(tmp_path)).counts() == {"completed": 3}


def test_a_completed_child_is_skipped(tmp_path):
    prepared = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    finish(prepared.children[0].run_dir)

    seen = []

    def run(child):
        seen.append(child.index)
        finish(child.run_dir)

    result = execute_sweep(sweep_dir_of(tmp_path), run)
    assert seen == [1]
    assert [c.index for c in result.skipped] == [0]


def test_a_missing_child_is_reported_not_silently_passed_over(tmp_path):
    prepared = prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)
    shutil.rmtree(prepared.children[0].run_dir)

    result = execute_sweep(sweep_dir_of(tmp_path), lambda child: finish(child.run_dir))
    assert [c.index for c in result.missing] == [0]
    assert [c.index for c in result.ran] == [1]


def test_a_retired_child_is_not_run(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)

    result = execute_sweep(sweep_dir_of(tmp_path), lambda child: finish(child.run_dir))
    assert [c.index for c in result.ran] == [0, 1]


def test_execution_can_be_narrowed_to_named_children(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    result = execute_sweep(
        sweep_dir_of(tmp_path), lambda child: finish(child.run_dir), only=[2]
    )
    assert [c.index for c in result.ran] == [2]


# --- stop and resume, with no driver-side state ------------------------------


def test_stopping_and_resuming_loses_no_work_and_repeats_none(tmp_path):
    """The driver's entire memory is the children's manifests. Kill it anywhere and
    start it again: nothing is lost, nothing is repeated, and it wrote nothing down
    to achieve that."""
    prepare(swept(tmp_path, {"epochs": [10, 20, 30, 40]}), tmp_path)
    done: list[int] = []

    def run_until_two(child):
        if len(done) >= 2:
            raise KeyboardInterrupt("I need the GPU")
        finish(child.run_dir)
        done.append(child.index)

    with pytest.raises(KeyboardInterrupt):
        execute_sweep(sweep_dir_of(tmp_path), run_until_two)

    assert done == [0, 1]
    assert refresh_sweep(sweep_dir_of(tmp_path)).counts() == {
        "completed": 2,
        "initialized": 2,
    }

    # A second pass, with no memory of the first beyond what is on disk.
    resumed: list[int] = []

    def run(child):
        resumed.append(child.index)
        finish(child.run_dir)

    execute_sweep(sweep_dir_of(tmp_path), run)
    assert resumed == [2, 3]  # neither repeated nor skipped
    assert refresh_sweep(sweep_dir_of(tmp_path)).counts() == {"completed": 4}


def test_the_cache_is_brought_up_to_date_even_when_a_pass_is_interrupted(tmp_path):
    prepare(swept(tmp_path, {"epochs": [10, 20]}), tmp_path)

    def run(child):
        finish(child.run_dir)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_sweep(sweep_dir_of(tmp_path), run)

    assert read_progress(sweep_dir_of(tmp_path))["counts"] == {
        "completed": 1,
        "initialized": 1,
    }


def test_deleting_the_cache_does_not_change_what_execution_does(tmp_path):
    """Stated as a behavioural claim rather than a claim about a file."""
    prepared = prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)
    finish(prepared.children[0].run_dir)
    refresh_sweep(sweep_dir_of(tmp_path))
    (sweep_dir_of(tmp_path) / SWEEP_MANIFEST_NAME).unlink()

    seen = []
    execute_sweep(
        sweep_dir_of(tmp_path),
        lambda child: (seen.append(child.index), finish(child.run_dir)),
    )
    assert seen == [1, 2]


def test_a_child_raising_propagates_and_leaves_the_rest_alone(tmp_path):
    """A defect in a child is a defect, not a sweep-level condition to absorb."""
    prepare(swept(tmp_path, {"epochs": [10, 20, 30]}), tmp_path)

    def run(child):
        if child.index == 1:
            raise ValueError("boom")
        finish(child.run_dir)

    with pytest.raises(ValueError, match="boom"):
        execute_sweep(sweep_dir_of(tmp_path), run)

    assert refresh_sweep(sweep_dir_of(tmp_path)).counts() == {
        "completed": 1,
        "initialized": 2,
    }
