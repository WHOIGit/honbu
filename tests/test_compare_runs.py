"""Run comparison (4.2), D7 version-aware reconciliation (4.3), and compatibility gating (4.4)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field

from honbu import (
    DIRECTIVE,
    ByteHash,
    ExperimentConfig,
    FieldHash,
    HashEntry,
    HashRegistry,
    HashedRuntimeConfig,
    OutputBlock,
    PortablePath,
    RefusalError,
    SourceHash,
    StrictModel,
)
from honbu.compare import (
    Objective,
    check_run_compatibility,
    compare_runs,
    compare_sweep,
    reconcile_hash,
    render_run_comparison,
)
from honbu.core import TemplateContext, hash_source, write_hash_entries
from honbu.flow import CommandSpec, ComposeSpec, Manifest, ProjectSpec, StepSpec, TargetSpec, open_run, resolve
from honbu.sweeps import SweepBlock, SweepDeclaration, prepare_sweep

CONTEXT = TemplateContext(
    now_local=datetime(2026, 8, 28, 9, 0, 0),
    now_utc=datetime(2026, 8, 28, 13, 0, 0, tzinfo=timezone.utc),
    seed="fixedseed",
)


class Outputs(OutputBlock):
    dir_template: Annotated[str | None, DIRECTIVE] = "{experiment.name}/{runtime.config_id}"


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
    epochs: int = 10
    lr: float = 0.1
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    sweep: SweepDeclaration = Field(default_factory=SweepBlock)


TARGET = TargetSpec("outputs.dir_template", "outputs.dir", "output_root")
TRAIN = CommandSpec(
    name=("train",),
    target=TARGET,
    hashes=("config_hash", "target_schema_hash", "dataset_hash"),
    steps=(StepSpec("fit"),),
)
HASHES = HashRegistry(
    [
        FieldHash("config_hash", include=["experiment", "epochs", "lr"], version="1"),
        SourceHash(
            "target_schema_hash",
            build=lambda ctx: {"version": "1", "classes": ["cat", "dog"]},
        ),
        SourceHash(
            "dataset_hash",
            build=lambda ctx: {"version": "1", "rows": ctx.get("rows", 100)},
            reads_inputs=True,
        ),
        ByteHash("weights_hash", locate=lambda ctx: ctx.get("weights_path")),
    ],
    root_type=Root,
)
PROJECT = ProjectSpec(root_type=Root, compose_spec=ComposeSpec(packaged=None), hashes=HASHES)


def _make_run(
    tmp_path: Path,
    *,
    name: str = "reef",
    epochs: int = 10,
    lr: float = 0.1,
    rows: int = 100,
    complete: bool = True,
    metrics: dict | None = None,
) -> Path:
    composed = {"experiment": {"name": name}, "epochs": epochs, "lr": lr}
    res = resolve(composed, command=TRAIN, project=PROJECT, context=CONTEXT, cwd=tmp_path)
    with open_run(res, composed=composed, project=PROJECT, argv_text="train") as run:
        run.complete_hash("dataset_hash", inputs={"rows": rows})
        if complete:
            with run.step("fit"):
                if metrics is not None:
                    run.record_best(epoch=epochs, metrics=metrics)
        run_dir = run.run_dir
    return run_dir


# --------------------------------------------------------------------------- #
# 4.3 D7 version-aware reconciliation
# --------------------------------------------------------------------------- #


def test_reconcile_same_version_and_encoder_compares_directly(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, rows=100, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=20, rows=100, metrics={"f1": 0.9})
    c = _make_run(tmp_path / "c", epochs=10, rows=999, metrics={"f1": 0.7})

    assert reconcile_hash(a, b, gate="dataset_hash", project=PROJECT).status == "match"
    assert reconcile_hash(a, c, gate="dataset_hash", project=PROJECT).status == "mismatch"


def test_reconcile_encoder_version_bump_rehashes_stored_sources(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, rows=100, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=20, rows=100, metrics={"f1": 0.9})

    # Simulate run b recorded under an older encoder_version with a different legacy digest
    entry_path = b / "config" / "hashes" / "dataset_hash.json"
    raw = json.loads(entry_path.read_text())
    raw["encoder_version"] = "0"
    raw["value"] = "sha256:legacy_old_encoder_digest"
    entry_path.write_text(json.dumps(raw))

    rec = reconcile_hash(a, b, gate="dataset_hash", project=PROJECT)
    assert rec.status == "reconciled_match"
    assert rec.method == "encoder_rehash"

    # Mutate stored source so re-hash detects genuine mismatch
    raw["source"]["rows"] = 555
    entry_path.write_text(json.dumps(raw))
    rec_bad = reconcile_hash(a, b, gate="dataset_hash", project=PROJECT)
    assert rec_bad.status == "reconciled_mismatch"
    assert rec_bad.method == "encoder_rehash"


def test_reconcile_byte_hash_is_always_directly_comparable(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=20, metrics={"f1": 0.9})

    write_hash_entries(
        a / "config",
        {
            "weights_hash": HashEntry(
                name="weights_hash", value="sha256:deadbeef", encoder_version="1", source=None
            )
        },
    )
    write_hash_entries(
        b / "config",
        {
            "weights_hash": HashEntry(
                name="weights_hash", value="sha256:deadbeef", encoder_version="2", source=None
            )
        },
    )
    rec = reconcile_hash(a, b, gate="weights_hash", project=PROJECT)
    assert rec.status == "match"
    assert rec.method == "byte_digest"


def test_reconcile_config_field_hash_diffs_resolved_yaml_trees(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=10, metrics={"f1": 0.85})
    c = _make_run(tmp_path / "c", epochs=30, metrics={"f1": 0.9})

    # Bump recipe version on run b and c
    for run_dir in (b, c):
        p = run_dir / "config" / "hashes" / "config_hash.json"
        raw = json.loads(p.read_text())
        raw["version"] = "2"
        raw["value"] = "sha256:v2_digest"
        p.write_text(json.dumps(raw))

    same = reconcile_hash(a, b, gate="config_hash", project=PROJECT)
    assert same.status == "reconciled_match"
    assert same.method == "resolved_tree_diff"

    diff = reconcile_hash(a, c, gate="config_hash", project=PROJECT)
    assert diff.status == "reconciled_mismatch"
    assert diff.method == "resolved_tree_diff"


def test_reconcile_domain_identity_key_compatible_vs_incompatible_shapes(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=20, metrics={"f1": 0.9})

    # Compatible key shape (only version bumped)
    p = b / "config" / "hashes" / "target_schema_hash.json"
    raw = json.loads(p.read_text())
    raw["version"] = "2"
    raw["source"]["version"] = "2"
    raw["value"] = hash_source(raw["source"])
    p.write_text(json.dumps(raw))

    rec = reconcile_hash(a, b, gate="target_schema_hash", project=PROJECT)
    assert rec.status == "reconciled_match"
    assert rec.method == "stored_source_diff"

    # Incompatible key shape (v2 added a new key)
    raw["source"]["normalize_exif"] = True
    raw["value"] = hash_source(raw["source"])
    p.write_text(json.dumps(raw))

    unrec = reconcile_hash(a, b, gate="target_schema_hash", project=PROJECT)
    assert unrec.status == "unreconcilable"
    assert "normalize_exif" in unrec.detail


# --------------------------------------------------------------------------- #
# 4.4 Reusable run compatibility gating
# --------------------------------------------------------------------------- #


def test_check_run_compatibility_warn_default_vs_strict_refuse(tmp_path):
    a = _make_run(tmp_path / "a", epochs=10, rows=100, metrics={"f1": 0.8})
    b = _make_run(tmp_path / "b", epochs=20, rows=100, metrics={"f1": 0.9})
    c = _make_run(tmp_path / "c", epochs=30, rows=200, metrics={"f1": 0.95})

    # Compatible strict check passes
    ok = check_run_compatibility(
        [a, b], gates=("dataset_hash", "target_schema_hash"), project=PROJECT, strict=True
    )
    assert ok.compatible is True
    assert ok.warnings == ()

    # Gate mismatch: advisory reports mismatch, strict raises RefusalError
    advisory = check_run_compatibility([a, c], gates=("dataset_hash",), project=PROJECT)
    assert advisory.compatible is False
    assert advisory.mismatched_gates == ("dataset_hash",)

    with pytest.raises(RefusalError, match="failed compatibility gating.*dataset_hash"):
        check_run_compatibility([a, c], gates=("dataset_hash",), project=PROJECT, strict=True)

    # Unreconcilable source shape: default warns, on_unreconcilable='refuse' refuses
    p = b / "config" / "hashes" / "target_schema_hash.json"
    raw = json.loads(p.read_text())
    raw["version"] = "2"
    raw["source"] = {"version": "2", "classes": ["cat", "dog"], "new_field": 1}
    raw["value"] = hash_source(raw["source"])
    p.write_text(json.dumps(raw))

    warned = check_run_compatibility(
        [a, b], gates=("target_schema_hash",), project=PROJECT, on_unreconcilable="warn"
    )
    assert warned.unreconcilable_gates == ("target_schema_hash",)
    assert any("target_schema_hash" in w for w in warned.warnings)

    with pytest.raises(RefusalError, match="unreconcilable gate 'target_schema_hash'"):
        check_run_compatibility(
            [a, b], gates=("target_schema_hash",), project=PROJECT, on_unreconcilable="refuse"
        )


# --------------------------------------------------------------------------- #
# 4.2 Run comparison and sweep rollup
# --------------------------------------------------------------------------- #


def test_compare_runs_ranks_completed_lists_incomplete_and_reports_gate_mismatch(tmp_path):
    good1 = _make_run(tmp_path / "r1", epochs=10, lr=0.1, rows=100, metrics={"val_loss": 0.4, "f1": 0.8})
    good2 = _make_run(tmp_path / "r2", epochs=20, lr=0.05, rows=100, metrics={"val_loss": 0.2, "f1": 0.92})
    incomplete = _make_run(tmp_path / "r3", epochs=30, lr=0.01, rows=100, complete=False)
    no_metric = _make_run(tmp_path / "r4", epochs=40, lr=0.02, rows=100, metrics={"other": 1.0})

    cmp = compare_runs(
        [good1, good2, incomplete, no_metric],
        gates=("dataset_hash", "target_schema_hash"),
        objective=Objective("val_loss", direction="minimize"),
        project=PROJECT,
    )
    assert cmp.comparable is True
    assert [r.metric_value for r in cmp.ranked] == [0.2, 0.4]
    assert [r.rank for r in cmp.ranked] == [1, 2]
    assert len(cmp.unranked) == 2
    assert any(u.state == "partial" for u in cmp.unranked)
    assert any("val_loss" in u.reason for u in cmp.unranked)
    diff_paths = {d.path for d in cmp.config_comparison.differences}
    assert {"epochs", "lr"} <= diff_paths

    rendered = render_run_comparison(cmp)
    assert "Compatibility: compatible" in rendered
    assert "Ranking (val_loss, minimize):" in rendered
    assert "Unranked:" in rendered

    # Mismatched gate marks not comparable without aborting the report
    different_ds = _make_run(tmp_path / "r5", epochs=10, lr=0.1, rows=999, metrics={"val_loss": 0.1})
    bad = compare_runs(
        [good1, different_ds],
        gates=("dataset_hash",),
        objective=Objective("val_loss", direction="minimize"),
        project=PROJECT,
    )
    assert bad.comparable is False
    assert "dataset_hash" in bad.compatibility.mismatched_gates


def test_compare_sweep_rolls_up_active_children_with_axis_values_and_missing(tmp_path):
    composed = {
        "experiment": {"name": "reef"},
        "epochs": 10,
        "lr": 0.1,
        "sweep": {"gate": "runtime.config_id", "axes": {"epochs": [10, 20, 30]}},
    }
    sweep_dir = tmp_path / "sweeps" / "s1"
    prep = prepare_sweep(
        composed,
        sweep_dir=sweep_dir,
        command=TRAIN,
        project=PROJECT,
        context=CONTEXT,
        cwd=tmp_path,
    )
    # Complete two children with different metrics, delete third
    for child, score in zip(prep.children[:2], (0.75, 0.91)):
        m = Manifest.load(child.run_dir)
        m.record_lifecycle_event("started")
        m.record_best(metrics={"f1": score})
        m.record_outcome()
    shutil.rmtree(prep.children[2].run_dir)

    rollup = compare_sweep(
        sweep_dir,
        gates=("target_schema_hash",),
        objective=Objective("f1", direction="maximize"),
        project=PROJECT,
    )
    assert rollup.comparable is True
    assert [r.metric_value for r in rollup.ranked] == [0.91, 0.75]
    assert rollup.ranked[0].axis_values == {"epochs": 20}
    assert len(rollup.unranked) == 1
    assert rollup.unranked[0].state == "missing"
