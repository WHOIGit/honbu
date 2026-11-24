"""N-way run comparison, version-aware reconciliation (D7), and compatibility gating.

Implements tasks 4.2, 4.3, and 4.4:
* :func:`reconcile_hash` — D7 version-aware reconciliation between two runs on one
  hash (direct digest, byte digest, encoder re-hash, resolved-tree diff, or
  key-compatible stored-source comparison minus ``version``).
* :func:`check_run_compatibility` — reusable compatibility gating across runs for
  ensembling, model merging, distillation, or cross-run validation.
* :func:`compare_runs` and :func:`compare_sweep` — N-way run comparison combining
  gate checks, configuration diffing (sharing :func:`compare_configs`), and
  objective ranking over each run's recorded ``best.metrics`` while listing
  incomplete or unranked runs gracefully.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from honbu.compare.configs import (
    Comparison,
    ComparisonLevel,
    compare_configs,
    render_comparison,
)
from honbu.core.blocks import CONFIG_HASH_NAME
from honbu.core.canonical import CANONICAL_ENCODER_VERSION, canonical_bytes, hash_source
from honbu.core.hashes import ByteHash, FieldHash, HashEntry, HashRegistry, read_hash_entries
from honbu.errors import RefusalError
from honbu.flow.compose import composed_config_path, resolved_config_path
from honbu.flow.manifest import Manifest
from honbu.flow.project import ProjectSpec
from honbu.flow.run import config_dir_for

ReconciliationStatus = Literal[
    "match",
    "mismatch",
    "reconciled_match",
    "reconciled_mismatch",
    "unreconcilable",
]
OnUnreconcilable = Literal["warn", "refuse"]
Direction = Literal["maximize", "minimize"]


@dataclass(frozen=True)
class ReconciliationResult:
    """Pairwise D7 reconciliation outcome for one hash between two runs."""

    gate: str
    status: ReconciliationStatus
    method: str
    detail: str = ""


@dataclass(frozen=True)
class GateCheck:
    """Result of checking one gate hash across an entire set of runs."""

    gate: str
    compatible: bool
    status: ReconciliationStatus
    values: tuple[str | None, ...]
    versions: tuple[str | None, ...]
    encoder_versions: tuple[str | None, ...]
    method: str
    detail: str = ""


@dataclass(frozen=True)
class CompatibilityReport:
    """Structured compatibility report across runs over caller-declared gates."""

    labels: tuple[str, ...]
    run_dirs: tuple[Path, ...]
    checks: tuple[GateCheck, ...]
    compatible: bool
    warnings: tuple[str, ...] = ()

    @property
    def mismatched_gates(self) -> tuple[str, ...]:
        return tuple(
            check.gate
            for check in self.checks
            if not check.compatible and check.status != "unreconcilable"
        )

    @property
    def unreconcilable_gates(self) -> tuple[str, ...]:
        return tuple(
            check.gate for check in self.checks if check.status == "unreconcilable"
        )


@dataclass(frozen=True)
class Objective:
    """Caller-supplied ranking objective reading each run's ``best.metrics``."""

    metric: str
    direction: Direction = "maximize"

    def __post_init__(self) -> None:
        if self.direction not in ("maximize", "minimize"):
            raise RefusalError(
                f"objective direction must be 'maximize' or 'minimize', not {self.direction!r}"
            )


@dataclass(frozen=True)
class RankedRun:
    """One completed run ranked by the requested objective."""

    rank: int
    label: str
    run_dir: Path
    state: str
    metric_value: float
    metrics: Mapping[str, Any]
    identities: Mapping[str, Any] = field(default_factory=dict)
    axis_values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UnrankedRun:
    """A run excluded from ranking (incomplete, missing metric, or missing dir)."""

    label: str
    run_dir: Path
    state: str
    reason: str
    identities: Mapping[str, Any] = field(default_factory=dict)
    axis_values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunComparison:
    """Structured N-way run comparison: gates, config diff, objective ranking."""

    labels: tuple[str, ...]
    run_dirs: tuple[Path, ...]
    compatibility: CompatibilityReport
    config_comparison: Comparison
    objective: Objective | None = None
    ranked: tuple[RankedRun, ...] = ()
    unranked: tuple[UnrankedRun, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def comparable(self) -> bool:
        return self.compatibility.compatible


# --------------------------------------------------------------------------- #
# loading run records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _LoadedRunHash:
    entry: HashEntry | None
    from_artifact: bool


@dataclass(frozen=True)
class _RunRecord:
    label: str
    run_dir: Path
    manifest: Manifest
    entries: Mapping[str, HashEntry]

    def hash_of(self, name: str) -> _LoadedRunHash:
        if name in self.entries:
            return _LoadedRunHash(entry=self.entries[name], from_artifact=True)
        recorded = (self.manifest.data.get("hashes") or {}).get(name)
        if isinstance(recorded, Mapping) and recorded.get("value") is not None:
            return _LoadedRunHash(
                entry=HashEntry(
                    name=name.split(".", 1)[0],
                    key=name.split(".", 1)[1] if "." in name else None,
                    value=str(recorded["value"]),
                    version=(
                        str(recorded["version"])
                        if recorded.get("version") is not None
                        else None
                    ),
                    provenance=(
                        str(recorded["provenance"])
                        if recorded.get("provenance") is not None
                        else None
                    ),
                    encoder_version=str(
                        self.manifest.data.get("encoder_version", CANONICAL_ENCODER_VERSION)
                    ),
                    source=None,
                ),
                from_artifact=False,
            )
        return _LoadedRunHash(entry=None, from_artifact=False)


def _default_labels(paths: Sequence[Path]) -> tuple[str, ...]:
    names = [p.name for p in paths]
    if len(set(names)) == len(names):
        return tuple(names)
    return tuple(f"{p.name}#{index}" for index, p in enumerate(paths))


def _load_run(path: Path | str, label: str) -> _RunRecord:
    run_dir = Path(path).resolve()
    manifest = Manifest.load(run_dir)
    entries = read_hash_entries(config_dir_for(run_dir, 1))
    return _RunRecord(label=label, run_dir=run_dir, manifest=manifest, entries=entries)


def _load_resolved_yaml(run_dir: Path) -> dict[str, Any] | None:
    try:
        path = resolved_config_path(run_dir)
    except RefusalError:
        try:
            path = composed_config_path(run_dir)
        except RefusalError:
            return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


# --------------------------------------------------------------------------- #
# D7 version-aware reconciliation (4.3)
# --------------------------------------------------------------------------- #


def _is_byte_hash(
    gate: str,
    left: _LoadedRunHash,
    right: _LoadedRunHash,
    hashes: HashRegistry | None,
) -> bool:
    if hashes is not None and gate in hashes.names:
        return isinstance(hashes.declaration(gate), ByteHash)
    return (
        left.from_artifact
        and right.from_artifact
        and left.entry is not None
        and right.entry is not None
        and left.entry.source is None
        and right.entry.source is None
    )


def _is_config_field_hash(
    gate: str,
    hashes: HashRegistry | None,
    config_hash_name: str,
) -> bool:
    if hashes is not None and gate in hashes.names:
        return isinstance(hashes.declaration(gate), FieldHash)
    return gate == config_hash_name


def reconcile_hash(
    left_run: Path | str,
    right_run: Path | str,
    *,
    gate: str,
    hashes: HashRegistry | None = None,
    root_type: type[BaseModel] | None = None,
    project: ProjectSpec | None = None,
    config_hash_name: str | None = None,
) -> ReconciliationResult:
    """Reconcile one hash between two runs following the D7 ladder."""
    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    resolved_hashes = (
        hashes if hashes is not None else (project.hashes if project is not None else None)
    )
    cfg_hash = (
        config_hash_name
        if config_hash_name is not None
        else (project.config_hash_name if project is not None else CONFIG_HASH_NAME)
    )
    left = _load_run(left_run, Path(left_run).name)
    right = _load_run(right_run, Path(right_run).name)
    return _reconcile_records(
        left,
        right,
        gate=gate,
        hashes=resolved_hashes,
        root_type=resolved_root,
        config_hash_name=cfg_hash,
    )


def _reconcile_records(
    left: _RunRecord,
    right: _RunRecord,
    *,
    gate: str,
    hashes: HashRegistry | None,
    root_type: type[BaseModel] | None,
    config_hash_name: str,
) -> ReconciliationResult:
    lhs = left.hash_of(gate)
    rhs = right.hash_of(gate)

    if lhs.entry is None and rhs.entry is None:
        return ReconciliationResult(
            gate=gate,
            status="unreconcilable",
            method="missing",
            detail=f"hash {gate!r} is absent from both {left.label!r} and {right.label!r}",
        )
    if lhs.entry is None or rhs.entry is None:
        missing_in = left.label if lhs.entry is None else right.label
        return ReconciliationResult(
            gate=gate,
            status="mismatch",
            method="missing",
            detail=f"hash {gate!r} is missing from {missing_in!r}",
        )

    a, b = lhs.entry, rhs.entry

    # 1. Byte-identity hash (no source) — raw file SHA-256 is always directly comparable.
    if _is_byte_hash(gate, lhs, rhs, hashes):
        if a.value == b.value:
            return ReconciliationResult(gate=gate, status="match", method="byte_digest")
        return ReconciliationResult(
            gate=gate,
            status="mismatch",
            method="byte_digest",
            detail=f"{gate!r} byte digests differ ({a.value} vs {b.value})",
        )

    # 2. Same recipe version and same encoder version -> direct digest compare.
    if a.version == b.version and a.encoder_version == b.encoder_version:
        if a.value == b.value:
            return ReconciliationResult(gate=gate, status="match", method="direct")
        return ReconciliationResult(
            gate=gate,
            status="mismatch",
            method="direct",
            detail=f"{gate!r} digests differ ({a.value} vs {b.value})",
        )

    # 3. Encoder version bump only -> re-hash stored source objects under current encoder.
    if a.version == b.version and a.encoder_version != b.encoder_version:
        if a.source is not None and b.source is not None:
            rehashed_a = hash_source(a.source)
            rehashed_b = hash_source(b.source)
            if rehashed_a == rehashed_b:
                return ReconciliationResult(
                    gate=gate,
                    status="reconciled_match",
                    method="encoder_rehash",
                    detail=(
                        f"reconciled encoder v{a.encoder_version} vs v{b.encoder_version} "
                        "by re-hashing stored sources"
                    ),
                )
            return ReconciliationResult(
                gate=gate,
                status="reconciled_mismatch",
                method="encoder_rehash",
                detail=(
                    f"{gate!r} stored sources differ after encoder re-hash "
                    f"(v{a.encoder_version} vs v{b.encoder_version})"
                ),
            )
        return ReconciliationResult(
            gate=gate,
            status="unreconcilable",
            method="unreconcilable",
            detail=(
                f"{gate!r} encoder versions differ (v{a.encoder_version} vs "
                f"v{b.encoder_version}) and stored source artifact(s) are missing"
            ),
        )

    # 4. Recipe version differs.
    # 4a. Config identity (FieldHash) -> diff stored config/resolved.yaml trees directly.
    if _is_config_field_hash(gate, hashes, config_hash_name):
        cfg_a = _load_resolved_yaml(left.run_dir)
        cfg_b = _load_resolved_yaml(right.run_dir)
        if cfg_a is not None and cfg_b is not None:
            diff_level: ComparisonLevel = "behavioral" if root_type is not None else "literal"
            cmp = compare_configs(
                (cfg_a, cfg_b),
                labels=(left.label, right.label),
                level=diff_level,
                root_type=root_type,
            )
            if not cmp.differences:
                return ReconciliationResult(
                    gate=gate,
                    status="reconciled_match",
                    method="resolved_tree_diff",
                    detail=(
                        f"reconciled {gate!r} recipe v{a.version} vs v{b.version} "
                        f"via {diff_level} resolved.yaml comparison"
                    ),
                )
            diff_paths = ", ".join(d.path for d in cmp.differences[:5])
            return ReconciliationResult(
                gate=gate,
                status="reconciled_mismatch",
                method="resolved_tree_diff",
                detail=(
                    f"{gate!r} recipe v{a.version} vs v{b.version} differ in "
                    f"resolved.yaml at: {diff_paths}"
                ),
            )
        return ReconciliationResult(
            gate=gate,
            status="unreconcilable",
            method="unreconcilable",
            detail=(
                f"{gate!r} recipe versions differ (v{a.version} vs v{b.version}) "
                "and resolved.yaml is unavailable for tree diffing"
            ),
        )

    # 4b. Domain identity -> compare stored source dicts minus "version" when key-compatible.
    if isinstance(a.source, Mapping) and isinstance(b.source, Mapping):
        src_a = {k: v for k, v in a.source.items() if k != "version"}
        src_b = {k: v for k, v in b.source.items() if k != "version"}
        keys_a, keys_b = set(src_a.keys()), set(src_b.keys())
        if keys_a == keys_b:
            if canonical_bytes(src_a) == canonical_bytes(src_b):
                return ReconciliationResult(
                    gate=gate,
                    status="reconciled_match",
                    method="stored_source_diff",
                    detail=(
                        f"reconciled {gate!r} recipe v{a.version} vs v{b.version} "
                        "by comparing key-compatible stored sources minus version"
                    ),
                )
            return ReconciliationResult(
                gate=gate,
                status="reconciled_mismatch",
                method="stored_source_diff",
                detail=(
                    f"{gate!r} stored sources differ across recipe v{a.version} vs v{b.version}"
                ),
            )
        only_a = sorted(keys_a - keys_b)
        only_b = sorted(keys_b - keys_a)
        return ReconciliationResult(
            gate=gate,
            status="unreconcilable",
            method="unreconcilable",
            detail=(
                f"{gate!r} recipe v{a.version} vs v{b.version} have incompatible source "
                f"key shapes (only in {left.label!r}: {only_a}; only in {right.label!r}: {only_b})"
            ),
        )

    return ReconciliationResult(
        gate=gate,
        status="unreconcilable",
        method="unreconcilable",
        detail=(
            f"{gate!r} recipe versions differ (v{a.version} vs v{b.version}) and "
            "stored source object(s) are unavailable"
        ),
    )


# --------------------------------------------------------------------------- #
# Reusable run compatibility gating (4.4)
# --------------------------------------------------------------------------- #


def check_run_compatibility(
    runs: Sequence[Path | str],
    *,
    gates: Sequence[str],
    labels: Sequence[str] | None = None,
    hashes: HashRegistry | None = None,
    root_type: type[BaseModel] | None = None,
    project: ProjectSpec | None = None,
    config_hash_name: str | None = None,
    on_unreconcilable: OnUnreconcilable = "warn",
    strict: bool = False,
) -> CompatibilityReport:
    """Verify caller-declared gate hashes across runs using D7 reconciliation.

    Supports ensembling, model merging, distillation, and cross-run validation
    while projects own artifact combination. Defaults to ``on_unreconcilable="warn"``
    (warn and proceed); pass ``on_unreconcilable="refuse"`` to refuse unreconcilable
    version mismatches or ``strict=True`` to refuse any gate mismatch or
    unreconcilable version.
    """
    if on_unreconcilable not in ("warn", "refuse"):
        raise RefusalError(
            f"on_unreconcilable must be 'warn' or 'refuse', not {on_unreconcilable!r}"
        )
    if len(runs) < 2:
        raise RefusalError("run compatibility check needs at least two runs")

    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    resolved_hashes = (
        hashes if hashes is not None else (project.hashes if project is not None else None)
    )
    cfg_hash = (
        config_hash_name
        if config_hash_name is not None
        else (project.config_hash_name if project is not None else CONFIG_HASH_NAME)
    )

    run_paths = tuple(Path(r).resolve() for r in runs)
    run_labels = tuple(labels) if labels is not None else _default_labels(run_paths)
    if len(run_labels) != len(run_paths):
        raise RefusalError("labels must have one entry per run")

    records = tuple(_load_run(path, label) for path, label in zip(run_paths, run_labels))
    checks: list[GateCheck] = []
    warnings: list[str] = []

    for gate in gates:
        loaded = [rec.hash_of(gate) for rec in records]
        values = tuple(item.entry.value if item.entry is not None else None for item in loaded)
        versions = tuple(item.entry.version if item.entry is not None else None for item in loaded)
        encoders = tuple(
            item.entry.encoder_version if item.entry is not None else None
            for item in loaded
        )

        pair_results = [
            _reconcile_records(
                records[0],
                other,
                gate=gate,
                hashes=resolved_hashes,
                root_type=resolved_root,
                config_hash_name=cfg_hash,
            )
            for other in records[1:]
        ]

        mismatches = [
            r for r in pair_results if r.status in ("mismatch", "reconciled_mismatch")
        ]
        unreconcilable = [r for r in pair_results if r.status == "unreconcilable"]
        reconciled = [r for r in pair_results if r.status == "reconciled_match"]

        if mismatches:
            first_bad = mismatches[0]
            checks.append(
                GateCheck(
                    gate=gate,
                    compatible=False,
                    status=first_bad.status,
                    values=values,
                    versions=versions,
                    encoder_versions=encoders,
                    method=first_bad.method,
                    detail=first_bad.detail,
                )
            )
        elif unreconcilable:
            first_unrec = unreconcilable[0]
            if on_unreconcilable == "refuse" or strict:
                raise RefusalError(
                    f"unreconcilable gate {gate!r} across {list(run_labels)}: "
                    f"{first_unrec.detail}"
                )
            warnings.append(f"unreconcilable gate {gate!r}: {first_unrec.detail}")
            checks.append(
                GateCheck(
                    gate=gate,
                    compatible=False,
                    status="unreconcilable",
                    values=values,
                    versions=versions,
                    encoder_versions=encoders,
                    method=first_unrec.method,
                    detail=first_unrec.detail,
                )
            )
        elif reconciled:
            first_rec = reconciled[0]
            checks.append(
                GateCheck(
                    gate=gate,
                    compatible=True,
                    status="reconciled_match",
                    values=values,
                    versions=versions,
                    encoder_versions=encoders,
                    method=first_rec.method,
                    detail=first_rec.detail,
                )
            )
        else:
            checks.append(
                GateCheck(
                    gate=gate,
                    compatible=True,
                    status="match",
                    values=values,
                    versions=versions,
                    encoder_versions=encoders,
                    method=pair_results[0].method if pair_results else "direct",
                )
            )

    all_compatible = all(check.compatible for check in checks)
    report = CompatibilityReport(
        labels=run_labels,
        run_dirs=run_paths,
        checks=tuple(checks),
        compatible=all_compatible,
        warnings=tuple(warnings),
    )

    if strict and not all_compatible:
        problems = "; ".join(
            f"{c.gate} ({c.detail or c.status})" for c in checks if not c.compatible
        )
        raise RefusalError(
            f"runs {list(run_labels)} failed compatibility gating: {problems}"
        )

    return report


# --------------------------------------------------------------------------- #
# N-way run comparison & sweep rollup (4.2)
# --------------------------------------------------------------------------- #


def compare_runs(
    runs: Sequence[Path | str],
    *,
    gates: Sequence[str] = (),
    objective: Objective | None = None,
    labels: Sequence[str] | None = None,
    level: ComparisonLevel | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    project: ProjectSpec | None = None,
    config_hash_name: str | None = None,
    on_unreconcilable: OnUnreconcilable = "warn",
    axis_values: Sequence[Mapping[str, Any]] | None = None,
) -> RunComparison:
    """Compare two or more runs: gate checks, N-way config diff, and objective ranking.

    Incomplete runs or runs missing ``objective.metric`` are reported gracefully in
    ``unranked`` with their state and reason rather than aborting comparison.
    """
    if len(runs) < 2:
        raise RefusalError("run comparison needs at least two runs")

    resolved_root = (
        root_type if root_type is not None else (project.root_type if project else None)
    )
    resolved_hashes = (
        hashes if hashes is not None else (project.hashes if project is not None else None)
    )

    run_paths = tuple(Path(r).resolve() for r in runs)
    run_labels = tuple(labels) if labels is not None else _default_labels(run_paths)
    if len(run_labels) != len(run_paths):
        raise RefusalError("labels must have one entry per run")
    if axis_values is not None and len(axis_values) != len(run_paths):
        raise RefusalError("axis_values must have one entry per run")

    compatibility = check_run_compatibility(
        run_paths,
        gates=gates,
        labels=run_labels,
        hashes=resolved_hashes,
        root_type=resolved_root,
        project=project,
        config_hash_name=config_hash_name,
        on_unreconcilable=on_unreconcilable,
        strict=False,
    )

    loaded_configs: list[dict[str, Any]] = []
    for path, label in zip(run_paths, run_labels):
        cfg = _load_resolved_yaml(path)
        if cfg is None:
            raise RefusalError(f"run {label!r} ({path}) holds no readable configuration bundle")
        loaded_configs.append(cfg)

    effective_level: ComparisonLevel = (
        level
        if level is not None
        else ("behavioral" if resolved_root is not None else "literal")
    )
    config_cmp = compare_configs(
        loaded_configs,
        level=effective_level,
        labels=run_labels,
        root_type=resolved_root,
        hashes=resolved_hashes,
        command=command,
    )

    ranked_candidates: list[tuple[float, str, Path, str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    unranked: list[UnrankedRun] = []

    if objective is not None:
        for idx, (path, label) in enumerate(zip(run_paths, run_labels)):
            manifest = Manifest.load(path)
            identities = dict(manifest.data.get("identities") or {})
            axes = dict(axis_values[idx]) if axis_values is not None else {}
            state = manifest.state
            if state != "completed":
                unranked.append(
                    UnrankedRun(
                        label=label,
                        run_dir=path,
                        state=state,
                        reason=f"run is {state}, not completed",
                        identities=identities,
                        axis_values=axes,
                    )
                )
                continue
            best = manifest.data.get("best")
            metrics = best.get("metrics") if isinstance(best, Mapping) else None
            if not isinstance(metrics, Mapping) or objective.metric not in metrics:
                unranked.append(
                    UnrankedRun(
                        label=label,
                        run_dir=path,
                        state=state,
                        reason=f"best.metrics has no {objective.metric!r}",
                        identities=identities,
                        axis_values=axes,
                    )
                )
                continue
            raw_val = metrics[objective.metric]
            if (
                isinstance(raw_val, bool)
                or not isinstance(raw_val, (int, float))
                or math.isnan(float(raw_val))
                or math.isinf(float(raw_val))
            ):
                unranked.append(
                    UnrankedRun(
                        label=label,
                        run_dir=path,
                        state=state,
                        reason=f"best.metrics[{objective.metric!r}] is not a finite number: {raw_val!r}",
                        identities=identities,
                        axis_values=axes,
                    )
                )
                continue
            ranked_candidates.append(
                (float(raw_val), label, path, state, dict(metrics), identities, axes)
            )

        ranked_candidates.sort(
            key=lambda item: item[0],
            reverse=(objective.direction == "maximize"),
        )

    ranked = tuple(
        RankedRun(
            rank=position,
            label=label,
            run_dir=path,
            state=state,
            metric_value=val,
            metrics=metrics,
            identities=identities,
            axis_values=axes,
        )
        for position, (val, label, path, state, metrics, identities, axes) in enumerate(
            ranked_candidates, start=1
        )
    )

    warnings = list(compatibility.warnings)
    if not compatibility.compatible:
        for check in compatibility.checks:
            if not check.compatible and check.status != "unreconcilable":
                warnings.append(f"gate {check.gate!r} mismatched: {check.detail}")

    return RunComparison(
        labels=run_labels,
        run_dirs=run_paths,
        compatibility=compatibility,
        config_comparison=config_cmp,
        objective=objective,
        ranked=ranked,
        unranked=tuple(unranked),
        warnings=tuple(warnings),
    )


def compare_sweep(
    sweep_dir: Path | str,
    *,
    gates: Sequence[str] = (),
    objective: Objective | None = None,
    level: ComparisonLevel | None = None,
    root_type: type[BaseModel] | None = None,
    hashes: HashRegistry | None = None,
    command: str | None = None,
    project: ProjectSpec | None = None,
    config_hash_name: str | None = None,
    on_unreconcilable: OnUnreconcilable = "warn",
) -> RunComparison:
    """Roll up and compare all active children of a prepared sweep (D6, D23)."""
    from honbu.sweeps.progress import refresh_sweep

    status = refresh_sweep(Path(sweep_dir))
    present = [child for child in status.current if not child.missing]
    missing_children = [child for child in status.current if child.missing]

    if len(present) < 2:
        raise RefusalError(
            f"sweep {status.sweep_id!r} needs at least two present active children "
            f"to compare (found {len(present)})"
        )

    labels = tuple(f"#{child.index} ({child.gate_value})" for child in present)
    result = compare_runs(
        [child.run_dir for child in present],
        gates=gates,
        objective=objective,
        labels=labels,
        level=level,
        root_type=root_type,
        hashes=hashes,
        command=command,
        project=project,
        config_hash_name=config_hash_name,
        on_unreconcilable=on_unreconcilable,
        axis_values=[child.values for child in present],
    )

    if not missing_children:
        return result

    extra_unranked = tuple(
        UnrankedRun(
            label=f"#{child.index} ({child.gate_value})",
            run_dir=child.run_dir,
            state=child.state,
            reason="child run directory is missing",
            axis_values=dict(child.values),
        )
        for child in missing_children
    )
    return RunComparison(
        labels=result.labels,
        run_dirs=result.run_dirs,
        compatibility=result.compatibility,
        config_comparison=result.config_comparison,
        objective=result.objective,
        ranked=result.ranked,
        unranked=result.unranked + extra_unranked,
        warnings=result.warnings
        + tuple(
            f"active child #{child.index} ({child.run_dir}) is missing"
            for child in missing_children
        ),
    )


def render_run_comparison(comparison: RunComparison) -> str:
    """Render a plain-text summary of gates, config diffs, and objective rankings."""
    lines: list[str] = []
    if comparison.compatibility.checks:
        verdict = "compatible" if comparison.comparable else "NOT comparable"
        lines.append(f"Compatibility: {verdict}")
        for check in comparison.compatibility.checks:
            detail = f" ({check.detail})" if check.detail else ""
            lines.append(f"  gate {check.gate}: {check.status} [{check.method}]{detail}")

    lines.append(render_comparison(comparison.config_comparison))

    if comparison.objective is not None:
        lines.append(
            f"Ranking ({comparison.objective.metric}, {comparison.objective.direction}):"
        )
        for item in comparison.ranked:
            axes = (
                f" {dict(item.axis_values)}" if item.axis_values else ""
            )
            lines.append(
                f"  #{item.rank} {item.label}: {comparison.objective.metric}={item.metric_value}{axes}"
            )
        if comparison.unranked:
            lines.append("Unranked:")
            for item in comparison.unranked:
                lines.append(f"  - {item.label} [{item.state}]: {item.reason}")

    if comparison.warnings:
        for warning in comparison.warnings:
            lines.append(f"Warning: {warning}")
    return "\n".join(lines)
