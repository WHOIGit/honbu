"""L1 mechanics: the decisions, asserted.

Every test here corresponds to a decision taken during design review. A failure
means a decision was reversed, not merely that code broke.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import Field, StringConstraints, ValidationError

from honbu import (
    DEFER,
    MANIFEST_NAME,
    DerivedMarker,
    Deferred,
    FannedHash,
    FieldHash,
    FromHash,
    HashRegistry,
    PathMarker,
    PortablePath,
    RefusalError,
    ResolvedString,
    SourceHash,
    StrictModel,
    TemplateContext,
    TemplateFunctionSpec,
    TemplateRegistry,
    atomic_write_text,
    backend_kwargs,
    canonical_bytes,
    check_field_path,
    excluded_source_paths,
    hash_source,
    marked_field_paths,
    prepare_scope,
    prepare_target,
    read_hash_entries,
    render_template,
    sanitize_path_component,
    sanitization_warning,
    unmarked_path_fields,
    validate_identity_fields,
    verify_hash_entries,
    write_hash_entries,
)
from honbu.errors import CanonicalizationError, DeclarationError, TemplateError
from honbu.mechanics.hashes import ByteHash, declared_path_exists, project_fields

# --------------------------------------------------------------------------- #
# schemas used across the tests
# --------------------------------------------------------------------------- #


class TargetBlock(StrictModel):
    kind: Literal["classification"] = "classification"
    manifest: PortablePath | None = None


class DataBlock(StrictModel):
    root: PortablePath
    targets: dict[str, TargetBlock] = Field(default_factory=dict)


class RuntimeBlock(StrictModel):
    seed: int = 0
    run_id: str = "{:coolname}"
    config_hash: ResolvedString = None


class TrainingBlock(StrictModel):
    epochs: int = 10
    patience: int | Deferred = DEFER


class Root(StrictModel):
    name: str
    runtime: RuntimeBlock = Field(default_factory=RuntimeBlock)
    data: DataBlock
    training: TrainingBlock = Field(default_factory=TrainingBlock)


def make_root(**overrides) -> Root:
    payload = {"name": "exp", "data": {"root": "/data/set"}} | overrides
    return Root.model_validate(payload)


def context() -> TemplateContext:
    return TemplateContext(
        now_local=datetime(2026, 8, 25, 14, 30, 0),
        now_utc=datetime(2026, 8, 25, 18, 30, 0, tzinfo=timezone.utc),
        seed="0123456789abcdef",
        extras={"source_run_dir": "/runs/prior"},
    )


# --------------------------------------------------------------------------- #
# canonical encoding
# --------------------------------------------------------------------------- #


def test_authored_order_is_irrelevant():
    assert hash_source({"a": 1, "b": 2}) == hash_source({"b": 2, "a": 1})


def test_negative_zero_folds():
    assert hash_source({"x": -0.0}) == hash_source({"x": 0.0})


def test_float_quantizes_to_twelve_significant_digits():
    assert canonical_bytes({"x": 0.1 + 0.2}) == canonical_bytes({"x": 0.3})


def test_int_and_float_stay_distinct():
    assert hash_source({"x": 1}) != hash_source({"x": 1.0})


def test_bool_and_int_stay_distinct():
    assert hash_source({"x": True}) != hash_source({"x": 1})


def test_unicode_is_raw_utf8_and_unnormalized():
    composed = unicodedata.normalize("NFC", "cafe\u0301")
    decomposed = unicodedata.normalize("NFD", "cafe\u0301")
    assert composed != decomposed
    assert canonical_bytes({"n": composed}) == b'{"n":"caf\xc3\xa9"}'
    # No NFC pass: a digest means "these bytes", so two renderings of one glyph
    # are two identities. Pinned deliberately rather than by accident.
    assert hash_source({"n": composed}) != hash_source({"n": decomposed})


def test_non_finite_is_refused_with_its_path():
    with pytest.raises(CanonicalizationError, match=r"a\.b: non-finite"):
        canonical_bytes({"a": {"b": math.inf}})


def test_non_string_key_is_refused_not_coerced():
    with pytest.raises(CanonicalizationError, match="not str"):
        canonical_bytes({"m": {1: "a"}})


def test_unencodable_type_names_the_path():
    with pytest.raises(CanonicalizationError, match=r"rows\.1\.when: date"):
        canonical_bytes({"rows": [{}, {"when": datetime(2026, 1, 1).date()}]})


def test_set_is_refused_because_it_has_no_canonical_order():
    with pytest.raises(CanonicalizationError, match="set is not canonically encodable"):
        canonical_bytes({"x": {1, 2}})


# --------------------------------------------------------------------------- #
# model base
# --------------------------------------------------------------------------- #


def test_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        Root.model_validate({"name": "e", "data": {"root": "/d"}, "typo": 1})


def test_assignment_is_validated():
    root = make_root()
    with pytest.raises(ValidationError):
        root.training.epochs = "many"


def test_invalid_default_is_caught_for_callers_who_never_override_it():
    class Bad(StrictModel):
        ratio: float = Field(default=1.5, ge=0.0, lt=1.0)

    with pytest.raises(ValidationError):
        Bad()


def test_whitespace_is_stripped_but_a_field_can_opt_out():
    class Block(StrictModel):
        name: str
        separator: Annotated[str, StringConstraints(strip_whitespace=False)] = " | "

    block = Block(name="  exp  ", separator=" | ")
    assert block.name == "exp"
    assert block.separator == " | "


def test_non_finite_survives_the_dump_so_the_encoder_can_refuse_it():
    class Block(StrictModel):
        x: float

    dumped = Block(x=math.inf).model_dump(mode="json")
    assert dumped["x"] == math.inf  # not None, which exclude_none would drop
    with pytest.raises(CanonicalizationError):
        canonical_bytes(dumped)


def test_deferred_knobs_are_omitted_from_backend_arguments():
    assert backend_kwargs(TrainingBlock()) == {"epochs": 10}
    assert backend_kwargs(TrainingBlock(patience=5)) == {"epochs": 10, "patience": 5}


def test_deferring_and_pinning_are_different_runs():
    assert hash_source(TrainingBlock().model_dump(mode="json")) != hash_source(
        TrainingBlock(patience=100).model_dump(mode="json")
    )


# --------------------------------------------------------------------------- #
# markers and walkers
# --------------------------------------------------------------------------- #


def test_marker_walk_emits_a_container_step():
    paths = marked_field_paths(Root, PathMarker)
    assert ("data", "root") in paths
    assert ("data", "targets", "*", "manifest") in paths


def test_derived_fields_are_excluded_from_sources():
    assert ("runtime", "config_hash") in marked_field_paths(Root, DerivedMarker)


def test_unmarked_path_guard_finds_a_bare_path_field():
    class Sloppy(StrictModel):
        good: PortablePath | None = None
        bad: Path | None = None

    assert unmarked_path_fields(Sloppy) == (("bad",),)


def test_paths_inside_containers_do_not_reach_a_digest():
    """The bug the WILDCARD exists for: exclusion used to silently miss containers."""
    left = make_root(data={"root": "/a", "targets": {"t": {"manifest": "/x/1.csv"}}})
    right = make_root(data={"root": "/b", "targets": {"t": {"manifest": "/y/2.csv"}}})
    declaration = FieldHash(name="config_hash", include=["name", "data", "training"])
    registry = HashRegistry([declaration], root_type=Root)
    assert (
        registry.compute({"config": left}).entries["config_hash"].value
        == registry.compute({"config": right}).entries["config_hash"].value
    )


def test_excluded_source_paths_covers_both_markers():
    paths = excluded_source_paths(Root)
    assert ("data", "root") in paths
    assert ("runtime", "config_hash") in paths


# --------------------------------------------------------------------------- #
# path portability
# --------------------------------------------------------------------------- #


def test_colons_are_sanitized_on_every_platform():
    assert sanitize_path_component("p2:multihead") == "p2_multihead"


def test_reserved_device_stems_are_altered():
    assert sanitize_path_component("NUL") == "_NUL"
    assert sanitize_path_component("com1.txt") == "_com1.txt"


def test_trailing_dots_and_spaces_are_dropped():
    assert sanitize_path_component("run. ") == "run"


def test_sanitization_is_reported():
    assert sanitization_warning("a:b", "a_b") is not None
    assert sanitization_warning("ab", "ab") is None


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #


def test_invocation_namespace_renders_clock_and_fresh_identity():
    root = make_root()
    values = root.model_dump(mode="json", exclude_none=True)
    result = render_template(
        "{name}/{:timestamp}_{:coolname}",
        values,
        context=context(),
        registry=TemplateRegistry(),
    )
    assert re.fullmatch(r"exp/2026-08-25_14-30-00_[a-z]+-[a-z]+", result.text)


def test_utc_token_is_marked_as_utc():
    result = render_template(
        "{:timestamp_utc}", {}, context=context(), registry=TemplateRegistry()
    )
    assert result.text == "2026-08-25_18-30-00Z"


def test_a_token_is_stable_within_one_render():
    result = render_template(
        "{:coolname}/{:coolname}", {}, context=context(), registry=TemplateRegistry()
    )
    first, _, second = result.text.partition("/")
    assert first == second


def test_deterministic_identity_from_a_value_is_stable_across_invocations():
    values = {"runtime": {"config_hash": "sha256:abc"}}
    kwargs = {"registry": TemplateRegistry()}
    first = render_template("{runtime.config_hash:coolname}", values, context=context(), **kwargs)
    second = render_template("{runtime.config_hash:coolname}", values, context=context(), **kwargs)
    assert first.text == second.text


def test_unknown_value_raises_instead_of_producing_a_plausible_name():
    with pytest.raises(TemplateError, match="unknown value 'runtime.config_hashh'"):
        render_template(
            "{runtime.config_hashh:coolname}", {}, context=context(), registry=TemplateRegistry()
        )


def test_unregistered_function_raises():
    with pytest.raises(TemplateError, match="unknown function 'nope'"):
        render_template("{:nope}", {}, context=context(), registry=TemplateRegistry())


def test_non_scalar_is_refused():
    values = make_root().model_dump(mode="json", exclude_none=True)
    with pytest.raises(TemplateError, match="not a scalar"):
        render_template("{training}", values, context=context(), registry=TemplateRegistry())


def test_bool_renders():
    result = render_template(
        "{flag}", {"flag": True}, context=context(), registry=TemplateRegistry()
    )
    assert result.text == "True"


def test_substituted_values_are_sanitized_and_warned_about():
    result = render_template(
        "{name}", {"name": "p2:multihead"}, context=context(), registry=TemplateRegistry()
    )
    assert result.text == "p2_multihead"
    assert result.warnings


def test_path_marked_fields_keep_their_separators():
    result = render_template(
        "{data.root}/x",
        {"data": {"root": "/data/set"}},
        context=context(),
        registry=TemplateRegistry(),
        path_fields=frozenset({"data.root"}),
    )
    assert result.text == "/data/set/x"


def test_whole_path_functions_keep_their_separators():
    result = render_template(
        "{:source_run_dir}/exports", {}, context=context(), registry=TemplateRegistry()
    )
    assert result.text == "/runs/prior/exports"


def test_override_policy_belongs_to_the_project():
    with pytest.raises(ValueError, match="does not allow overrides"):
        TemplateRegistry(allow_override=False).register(
            TemplateFunctionSpec("slug", lambda ctx, v: "x")
        )
    permissive = TemplateRegistry(allow_override=True)
    permissive.register(TemplateFunctionSpec("slug", lambda ctx, v: "x"))
    assert permissive.get("slug").function(context(), "anything") == "x"


# --------------------------------------------------------------------------- #
# hash declarations and artifacts
# --------------------------------------------------------------------------- #


def test_declared_path_typo_fails_at_registry_construction():
    with pytest.raises(DeclarationError, match="trainng"):
        HashRegistry([FieldHash(name="config_hash", include=["trainng"])], root_type=Root)


def test_declared_paths_do_not_reach_inside_containers():
    assert declared_path_exists(Root, "data.root")
    assert not declared_path_exists(Root, "data.targets.kind")


def test_command_scoped_include_narrows_the_source():
    declaration = FieldHash(
        name="config_hash",
        include={"train": ["name", "training"], "inspect": ["name"]},
    )
    registry = HashRegistry([declaration], root_type=Root)
    root = make_root()
    train = registry.compute({"config": root, "command": "train"})
    inspect = registry.compute({"config": root, "command": "inspect"})
    assert set(train.entries["config_hash"].source) == {"name", "training"}
    assert train.entries["config_hash"].value != inspect.entries["config_hash"].value


def test_config_identity_carries_no_version_token():
    registry = HashRegistry([FieldHash(name="config_hash", include=["name"])], root_type=Root)
    entry = registry.compute({"config": make_root()}).entries["config_hash"]
    assert entry.version is None
    assert "version" not in entry.source


def test_source_hash_takes_its_version_from_the_source_it_hashed():
    declaration = SourceHash(
        name="target_schema_hash",
        build=lambda ctx: {"version": "2", "box_format": "xyxy"},
        provenance="constant",
    )
    entry = HashRegistry([declaration]).compute({}).entries["target_schema_hash"]
    assert entry.version == "2"
    assert entry.provenance == "constant"


def test_source_hash_without_a_version_is_a_declaration_error():
    declaration = SourceHash(name="bad", build=lambda ctx: {"a": 1})
    with pytest.raises(DeclarationError, match="must declare a version"):
        HashRegistry([declaration]).compute({})


def test_absent_inputs_defer_rather_than_fail(tmp_path):
    declarations = [
        SourceHash(name="dataset_hash", build=lambda ctx: None, reads_inputs=True),
        ByteHash(name="checkpoint_hash", locate=lambda ctx: None),
        FieldHash(name="config_hash", include=["name"]),
    ]
    result = HashRegistry(declarations, root_type=Root).compute(
        {"config": make_root()}, input_reading=True
    )
    assert set(result.deferred) == {"dataset_hash", "checkpoint_hash"}
    assert set(result.entries) == {"config_hash"}


def test_byte_hash_has_no_source_and_is_unverifiable(tmp_path):
    blob = tmp_path / "weights.pt"
    blob.write_bytes(b"weights")
    declaration = ByteHash(name="checkpoint_hash", locate=lambda ctx: blob)
    entries = HashRegistry([declaration]).compute({}, input_reading=True).entries
    assert entries["checkpoint_hash"].source is None
    assert verify_hash_entries(entries) == {"checkpoint_hash": None}


def test_artifacts_round_trip_and_verify(tmp_path):
    declarations = [
        FieldHash(name="config_hash", include=["name", "training"]),
        SourceHash(name="target_schema_hash", build=lambda ctx: {"version": "2", "a": 1}),
    ]
    registry = HashRegistry(declarations, root_type=Root)
    entries = registry.compute({"config": make_root()}).entries
    write_hash_entries(tmp_path, entries)

    assert (tmp_path / "hashes" / "config_hash.json").is_file()
    reloaded = read_hash_entries(tmp_path)
    assert reloaded == entries
    assert verify_hash_entries(reloaded) == {"config_hash": True, "target_schema_hash": True}


def test_a_tampered_source_fails_verification(tmp_path):
    registry = HashRegistry([FieldHash(name="config_hash", include=["name"])], root_type=Root)
    entries = registry.compute({"config": make_root()}).entries
    tampered = {"config_hash": entries["config_hash"].model_copy(update={"source": {"name": "x"}})}
    assert verify_hash_entries(tampered) == {"config_hash": False}


# --------------------------------------------------------------------------- #
# atomic writes
# --------------------------------------------------------------------------- #


def test_atomic_write_replaces_and_leaves_no_residue(tmp_path):
    target = tmp_path / "run_manifest.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
    assert [p.name for p in tmp_path.iterdir()] == ["run_manifest.json"]


def test_a_failed_write_leaves_the_previous_artifact_intact(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    atomic_write_text(target, "original")

    import honbu.mechanics.io as io_module

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(io_module.os, "replace", explode)
    with pytest.raises(OSError):
        atomic_write_text(target, "replacement")
    assert target.read_text() == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


# --------------------------------------------------------------------------- #
# collision-safe targets
# --------------------------------------------------------------------------- #


def test_a_target_outside_the_containment_root_is_refused(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    with pytest.raises(RefusalError, match="outside its containment root"):
        prepare_target(tmp_path / "elsewhere", containment_root=root)


def test_the_containment_root_itself_is_refused(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    with pytest.raises(RefusalError, match="containment root itself"):
        prepare_target(root, containment_root=root)


def test_an_empty_target_is_fine(tmp_path):
    root = tmp_path / "runs"
    target, warnings = prepare_target(root / "exp" / "a", containment_root=root)
    assert target.is_dir()
    assert not warnings


def test_a_non_empty_target_is_refused_and_describes_the_occupant(tmp_path):
    root = tmp_path / "runs"
    target = root / "exp" / "a"
    target.mkdir(parents=True)
    (target / MANIFEST_NAME).write_text(
        '{"state": "failed", "command": ["train"],'
        ' "identities": {"config_id": "brave-otter"}}'
    )
    with pytest.raises(RefusalError, match="holds failed train run"):
        prepare_target(target, containment_root=root)


def test_the_occupant_description_carries_progress_and_best(tmp_path):
    """The refusal must give a human enough to decide without opening the file.

    This replaced --force: a second flag added ceremony, a good description adds
    information.
    """
    root = tmp_path / "runs"
    target = root / "exp" / "a"
    target.mkdir(parents=True)
    (target / MANIFEST_NAME).write_text(
        '{"state": "completed", "command": ["train"], "progress": {"epoch": 40},'
        ' "best": {"metrics": {"map50": 0.613}}}'
    )
    with pytest.raises(RefusalError) as caught:
        prepare_target(target, containment_root=root)
    message = str(caught.value)
    assert "completed train run" in message
    assert "40 epochs" in message
    assert "map50 0.613" in message


def test_clobber_clears_a_run_but_keeps_the_directory(tmp_path):
    root = tmp_path / "runs"
    target = root / "exp" / "a"
    (target / "checkpoints").mkdir(parents=True)
    (target / MANIFEST_NAME).write_text('{"state": "failed"}')
    inode = target.stat().st_ino
    cleared, _ = prepare_target(target, containment_root=root, clobber=True)
    assert list(cleared.iterdir()) == []
    assert cleared.stat().st_ino == inode


def test_clobbering_a_completed_run_no_longer_needs_a_second_flag(tmp_path):
    """--force is gone. It never fired under a timestamped template and fired on
    every deliberate re-run under a deterministic one."""
    root = tmp_path / "runs"
    target = root / "exp" / "a"
    target.mkdir(parents=True)
    (target / MANIFEST_NAME).write_text('{"state": "completed", "command": ["train"]}')
    cleared, _ = prepare_target(target, containment_root=root, clobber=True)
    assert list(cleared.iterdir()) == []


def test_case_colliding_sibling_warns(tmp_path):
    root = tmp_path / "runs"
    (root / "Exp").mkdir(parents=True)
    _, warnings = prepare_target(root / "exp", containment_root=root)
    assert any("differ only in case" in warning for warning in warnings)


# --------------------------------------------------------------------------- #
# augmenting an existing run
# --------------------------------------------------------------------------- #


def _seed_run(root: Path) -> Path:
    run_dir = root / "exp" / "a"
    (run_dir / "checkpoints").mkdir(parents=True)
    (run_dir / "checkpoints" / "last.pt").write_text("weights")
    (run_dir / MANIFEST_NAME).write_text('{"state": "completed", "command": ["train"]}')
    return run_dir


def test_augmenting_requires_an_existing_run(tmp_path):
    root = tmp_path / "runs"
    (root / "exp" / "a").mkdir(parents=True)
    with pytest.raises(RefusalError, match="holds no manifest.json"):
        prepare_scope(root / "exp" / "a", (), containment_root=root)


def test_clobbering_a_leaf_cannot_reach_the_rest_of_the_run(tmp_path):
    """The whole reason scope is declared rather than flagged: rewriting figures
    must not be able to delete checkpoints."""
    root = tmp_path / "runs"
    run_dir = _seed_run(root)
    figures = run_dir / "figures"
    figures.mkdir()
    (figures / "old.png").write_text("stale")

    prepare_scope(run_dir, (figures,), containment_root=root, clobber=True)

    assert list(figures.iterdir()) == []
    assert (run_dir / "checkpoints" / "last.pt").read_text() == "weights"
    assert (run_dir / MANIFEST_NAME).is_file()


def test_a_non_empty_leaf_is_refused_without_clobber(tmp_path):
    root = tmp_path / "runs"
    run_dir = _seed_run(root)
    figures = run_dir / "figures"
    figures.mkdir()
    (figures / "old.png").write_text("stale")
    with pytest.raises(RefusalError, match="not empty"):
        prepare_scope(run_dir, (figures,), containment_root=root)


def test_a_leaf_outside_the_containment_root_is_still_refused(tmp_path):
    root = tmp_path / "runs"
    run_dir = _seed_run(root)
    with pytest.raises(RefusalError, match="outside its containment root"):
        prepare_scope(run_dir, (tmp_path / "elsewhere",), containment_root=root)


# --------------------------------------------------------------------------- #
# fanned-out hashes
# --------------------------------------------------------------------------- #


def _head_sources(ctx):
    """dojo's per-head identity: one entry per key in model.heads."""
    return {
        name: {"version": "1", "head": name, "classes": spec["classes"]}
        for name, spec in ctx["heads"].items()
    }


def test_a_fanned_declaration_yields_one_entry_per_key():
    registry = HashRegistry([FannedHash(name="head_hash", build=_head_sources)])
    result = registry.compute(
        {"heads": {"dorsal": {"classes": 3}, "caudal": {"classes": 5}}}
    )
    assert set(result.entries) == {"head_hash.dorsal", "head_hash.caudal"}
    assert result.entries["head_hash.dorsal"].key == "dorsal"
    assert result.entries["head_hash.dorsal"].name == "head_hash"


def test_fanned_entries_differ_by_their_own_source():
    registry = HashRegistry([FannedHash(name="head_hash", build=_head_sources)])
    entries = registry.compute(
        {"heads": {"dorsal": {"classes": 3}, "caudal": {"classes": 5}}}
    ).entries
    assert entries["head_hash.dorsal"].value != entries["head_hash.caudal"].value


def test_a_sequence_fans_out_by_index():
    registry = HashRegistry(
        [FannedHash(name="stage_hash", build=lambda ctx: [{"version": "1", "n": 0},
                                                          {"version": "1", "n": 1}])]
    )
    assert set(registry.compute({}).entries) == {"stage_hash.0", "stage_hash.1"}


def test_each_fanned_entry_gets_its_own_artifact(tmp_path):
    registry = HashRegistry([FannedHash(name="head_hash", build=_head_sources)])
    result = registry.compute({"heads": {"dorsal": {"classes": 3}, "caudal": {"classes": 5}}})
    write_hash_entries(tmp_path, result.entries)
    written = sorted(p.name for p in (tmp_path / "hashes").iterdir())
    assert written == ["head_hash.caudal.json", "head_hash.dorsal.json"]

    read_back = read_hash_entries(tmp_path)
    assert set(read_back) == set(result.entries)
    assert all(verify_hash_entries(read_back).values())


def test_a_fanned_key_is_sanitized_into_its_filename(tmp_path):
    registry = HashRegistry(
        [FannedHash(name="head_hash", build=lambda ctx: {"a/b": {"version": "1"}})]
    )
    write_hash_entries(tmp_path, registry.compute({}).entries)
    assert [p.name for p in (tmp_path / "hashes").iterdir()] == ["head_hash.a_b.json"]


def test_keys_that_sanitize_alike_are_refused_rather_than_overwriting(tmp_path):
    """Silently overwriting would leave a plausible artifact for a head that never
    got one."""
    registry = HashRegistry(
        [
            FannedHash(
                name="head_hash",
                build=lambda ctx: {"a/b": {"version": "1"}, "a:b": {"version": "1"}},
            )
        ]
    )
    result = registry.compute({})
    with pytest.raises(RefusalError, match="both sanitize to"):
        write_hash_entries(tmp_path, result.entries)


def test_a_fanned_name_may_not_contain_a_dot():
    with pytest.raises(DeclarationError, match="may not contain a dot"):
        FannedHash(name="head.hash", build=lambda ctx: {})


def test_a_fanned_build_returning_the_wrong_shape_is_refused():
    registry = HashRegistry([FannedHash(name="head_hash", build=lambda ctx: "nope")])
    with pytest.raises(DeclarationError, match="must return a mapping or a sequence"):
        registry.compute({})


def test_a_fanned_source_must_carry_its_version():
    registry = HashRegistry(
        [FannedHash(name="head_hash", build=lambda ctx: {"dorsal": {"classes": 3}})]
    )
    with pytest.raises(DeclarationError, match=r"head_hash\.dorsal: source must declare"):
        registry.compute({})


def test_a_fanned_hash_defers_as_a_whole():
    registry = HashRegistry(
        [FannedHash(name="head_hash", build=lambda ctx: None, reads_inputs=True)]
    )
    result = registry.compute({})
    assert result.deferred == ("head_hash",)
    assert result.entries == {}


def test_a_fanned_hash_cannot_feed_an_identity_field():
    """One field cannot hold many digests."""

    class Block(StrictModel):
        head_hash: Annotated[ResolvedString, FromHash("head_hash")] = None

    class RootF(StrictModel):
        block: Block = Field(default_factory=Block)

    registry = HashRegistry([FannedHash(name="head_hash", build=_head_sources)])
    with pytest.raises(DeclarationError, match="fans out to one entry per key"):
        validate_identity_fields(RootF, registry)


# --------------------------------------------------------------------------- #
# the framework reserves no hash name (D21)
# --------------------------------------------------------------------------- #


def test_the_framework_binds_no_hash_of_its_own_accord():
    """No shipped default may reference a field the framework does not guarantee.

    An earlier version defaulted `config_id` to a template over `config_hash` and
    bound `config_hash` to a reserved declaration to make that default work — which
    made declaring a hash mandatory, contradicting "identities are declared, not
    inferred". Both halves are gone: no binding, and a null default.
    """
    from honbu.mechanics.blocks import RuntimeConfig
    from honbu.mechanics.markers import identity_bindings

    assert identity_bindings(RuntimeConfig) == ()
    assert RuntimeConfig.model_fields["config_id"].default is None
    assert RuntimeConfig().config_hash is None


def test_a_project_declaring_no_hashes_is_valid():
    """A project that hashes nothing is a project, not a misconfiguration."""
    from pathlib import Path

    from pydantic import Field

    from honbu.mechanics.blocks import ExperimentConfig, RuntimeConfig
    from honbu.mechanics.markers import PortablePath

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: PortablePath = Path("./runs")

    assert validate_identity_fields(Root, HashRegistry([])) == ()


def test_binding_a_configuration_identity_is_two_lines():
    """The opt-in the adoption guide leads with, exercised as documentation."""
    from pathlib import Path
    from typing import Annotated

    from pydantic import Field

    from honbu.mechanics.blocks import CONFIG_HASH_NAME, ExperimentConfig, RuntimeConfig
    from honbu.mechanics.markers import (
        FromHash,
        PortablePath,
        ResolvedString,
        TemplatedIdentity,
    )

    class Runtime(RuntimeConfig):
        config_hash: Annotated[ResolvedString, FromHash(CONFIG_HASH_NAME)] = None
        config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: Runtime = Field(default_factory=Runtime)
        output_root: PortablePath = Path("./runs")

    registry = HashRegistry([FieldHash(name=CONFIG_HASH_NAME, include=["experiment"])])
    assert validate_identity_fields(Root, registry) == ((("runtime", "config_hash"), "config_hash"),)


def test_sweep_membership_cannot_enter_a_digest():
    """A sweep child's digest must equal the same configuration run by hand.

    `sweep_id` is assigned rather than authored, so it carries the derived and
    resolved-only markers. Before it did, a project that included the runtime block
    in a field hash got a child whose digest incorporated its sweep membership —
    making it incomparable to a hand-run baseline, silently.
    """
    from pathlib import Path

    from pydantic import Field

    from honbu.mechanics.blocks import ExperimentConfig, RuntimeConfig
    from honbu.mechanics.markers import PortablePath

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: PortablePath = Path("./runs")

    declaration = FieldHash(name="config_hash", include=["experiment", "runtime"])
    in_sweep = Root(
        experiment={"name": "x"}, runtime=RuntimeConfig(seed=7, sweep_id="brave-otter")
    )
    by_hand = Root(experiment={"name": "x"}, runtime=RuntimeConfig(seed=7))

    assert project_fields(declaration, {"config": in_sweep}) == project_fields(
        declaration, {"config": by_hand}
    )


def test_continuing_from_a_sweep_child_does_not_inherit_membership():
    """Resolved-only, so the continuation is a new run rather than a member."""
    from honbu.flow.resolve import strip_resolved_only
    from pathlib import Path

    from pydantic import Field

    from honbu.mechanics.blocks import ExperimentConfig, RuntimeConfig
    from honbu.mechanics.markers import PortablePath

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
        output_root: PortablePath = Path("./runs")

    parent = {"experiment": {"name": "x"}, "runtime": {"seed": 7, "sweep_id": "brave-otter"}}
    assert "sweep_id" not in strip_resolved_only(parent, Root)["runtime"]


# --------------------------------------------------------------------------- #
# addressable field paths: the two checks a sweep axis needs
# --------------------------------------------------------------------------- #


class Network(StrictModel):
    hidden_dims: list[int] = Field(default_factory=lambda: [512])
    dropout: float = 0.1


class HeadBlock(StrictModel):
    target: str
    network: Network = Field(default_factory=Network)


class Source(StrictModel):
    uri: str


class ModelBlock(StrictModel):
    backbone: str
    heads: dict[str, HeadBlock] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)


class Axes(StrictModel):
    model: ModelBlock
    epochs: int = 10


COMPOSED_AXES = {
    "model": {
        "backbone": "resnet50",
        "heads": {"species": {"target": "species"}, "damage": {"target": "damage"}},
        "sources": [{"uri": "a"}, {"uri": "b"}],
    }
}


def _problem(dotted, **kwargs):
    return check_field_path(Axes, dotted, **kwargs)


def test_an_ordinary_path_is_addressable():
    assert _problem("epochs") is None
    assert _problem("model.backbone") is None


def test_an_unknown_field_names_the_segment():
    problem = _problem("model.bakcbone")
    assert problem.kind == "unknown_field" and problem.segment == "bakcbone"


def test_a_container_is_not_traversed_unless_asked():
    """Declarations that must name a whole container keep their old behavior."""
    problem = _problem("model.heads.species.target")
    assert problem.kind == "not_traversable"
    assert declared_path_exists(Axes, "model.heads.species.target") is False
    assert declared_path_exists(Axes, "model.heads") is True


def test_a_container_step_is_traversable_wherever_it_occurs():
    """Not merely the first one, and the path continues past it."""
    assert _problem("model.heads.species.target", traverse_containers=True) is None
    assert (
        _problem("model.heads.species.network.dropout", traverse_containers=True) is None
    )
    assert (
        _problem("model.heads.species.network.hidden_dims", traverse_containers=True)
        is None
    )


def test_the_schema_check_catches_a_misspelled_leaf_below_a_container():
    problem = _problem("model.heads.species.network.dropuot", traverse_containers=True)
    assert problem.kind == "unknown_field" and problem.segment == "dropuot"


def test_only_the_data_check_catches_a_misspelled_container_key():
    """A mapping's schema admits any key, so the schema check cannot see this.

    Without the data check the axis would fabricate a head named 'speces', which
    surfaces much later as a per-child validation failure.
    """
    dotted = "model.heads.speces.network.dropout"
    assert _problem(dotted, traverse_containers=True) is None  # schema alone: fine
    problem = _problem(dotted, data=COMPOSED_AXES, traverse_containers=True)
    assert problem.kind == "missing_key" and problem.segment == "speces"
    assert "species" in problem.message  # names what is actually there


def test_presence_is_required_only_at_container_steps():
    """A schema-defaulted leaf is legitimately absent from the composed tree."""
    assert "network" not in COMPOSED_AXES["model"]["heads"]["species"]
    assert (
        _problem(
            "model.heads.species.network.dropout",
            data=COMPOSED_AXES,
            traverse_containers=True,
        )
        is None
    )


def test_a_sequence_is_addressed_by_index():
    assert _problem("model.sources.0.uri", data=COMPOSED_AXES, traverse_containers=True) is None
    out_of_range = _problem(
        "model.sources.7.uri", data=COMPOSED_AXES, traverse_containers=True
    )
    assert out_of_range.kind == "missing_key" and "2 entries" in out_of_range.message
    not_a_number = _problem(
        "model.sources.first.uri", data=COMPOSED_AXES, traverse_containers=True
    )
    assert not_a_number.kind == "bad_index"


def test_the_container_key_itself_may_be_the_leaf():
    assert _problem("model.heads.species", data=COMPOSED_AXES, traverse_containers=True) is None
