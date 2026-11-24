"""The resolver: the eight phases, the two views, and what may not be assigned."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pytest
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
    validate_identity_fields,
)
from honbu.core import TemplateContext, hash_source
from honbu.errors import DeclarationError, TemplateError
from honbu.flow import CommandSpec, ComposeSpec, ProjectSpec, Resolution, TargetSpec, resolve

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
    dir_template: str | None = "{experiment.name:slug}/{:timestamp}"
    metrics: OutputLeaf = Field(default_factory=lambda: OutputLeaf(dir=Path("metrics")))
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

TRAIN = CommandSpec(name=("train",), target=TARGET, hashes=("config_hash", "dataset_hash"))

HASHES = HashRegistry(
    [
        FieldHash(name="config_hash", include=["experiment", "data", "epochs"]),
        SourceHash(
            name="dataset_hash",
            build=lambda ctx: {"version": "1", "scanned": True},
            reads_inputs=True,
        ),
    ],
    root_type=Root,
)

COMPOSED = {"experiment": {"name": "reef v3"}, "data": {"uri": "./datasets/coco8"}}


def _resolve(composed=None, *, command=TRAIN, cwd, **kwargs) -> Resolution:
    return resolve(
        composed or COMPOSED,
        root_type=Root,
        command=command,
        hashes=HASHES,
        context=CONTEXT,
        cwd=cwd,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# phase order
# --------------------------------------------------------------------------- #


def test_an_identity_is_assigned_before_the_template_renders(tmp_path):
    """Assigning digests (4) before any template renders (5, then 6) is the
    load-bearing part of the order."""

    class Templated(TrainingOutputs):
        dir_template: str | None = "{runtime.config_hash:coolname}"

    class RootT(Root):
        training_outputs: Templated = Field(default_factory=Templated)

    res = resolve(
        COMPOSED,
        root_type=RootT,
        command=TRAIN,
        hashes=HashRegistry(
            [FieldHash(name="config_hash", include=["experiment", "data", "epochs"])],
            root_type=RootT,
        ),
        context=CONTEXT,
        cwd=tmp_path,
    )
    rendered = str(res.portable.training_outputs.dir)
    assert rendered and "config_hash" not in rendered
    assert "-" in rendered  # a coolname, not a raw digest


def test_input_paths_are_absolutized_and_output_paths_are_not(tmp_path):
    res = _resolve(cwd=tmp_path)
    assert res.portable.data.uri.is_absolute()
    assert not res.portable.training_outputs.dir.is_absolute()


def test_an_input_relative_path_does_not_reach_the_digest(tmp_path):
    """Phases 2 and 3 are independent only because paths never enter a digest."""
    a = _resolve(cwd=tmp_path)
    b = _resolve(cwd=tmp_path / "elsewhere")
    (tmp_path / "elsewhere").mkdir()
    assert a.hashes["config_hash"].value == b.hashes["config_hash"].value


# --------------------------------------------------------------------------- #
# the two views
# --------------------------------------------------------------------------- #


def test_the_absolute_view_is_directly_usable(tmp_path):
    res = _resolve(cwd=tmp_path)
    assert res.config.training_outputs.dir.is_absolute()
    assert res.config.training_outputs.metrics.dir.is_absolute()
    assert res.config.training_outputs.metrics.dir.parent == res.config.training_outputs.dir


def test_the_portable_view_keeps_output_paths_relative(tmp_path):
    res = _resolve(cwd=tmp_path)
    assert res.portable.training_outputs.dir == Path("reef-v3/2026-08-26_14-30-00")
    assert res.portable.training_outputs.metrics.dir == Path("metrics")


def test_both_views_are_the_same_configuration(tmp_path):
    """Paths are excluded from digests, so the split cannot create two truths."""
    res = _resolve(cwd=tmp_path)
    dump = lambda cfg: {  # noqa: E731
        k: v for k, v in cfg.model_dump(mode="json").items() if k != "training_outputs"
    }
    assert hash_source(dump(res.config)) == hash_source(dump(res.portable))


def test_a_cwd_relative_output_directory_is_stored_absolute(tmp_path):
    """Which is what makes the stored form lossless: a relative value left in a
    resolved configuration is unambiguously output-root-relative."""
    composed = {**COMPOSED, "training_outputs": {"dir_template": "./scratch/out"}}
    res = _resolve(composed, cwd=tmp_path)
    assert res.portable.training_outputs.dir.is_absolute()


# --------------------------------------------------------------------------- #
# deferral
# --------------------------------------------------------------------------- #


def test_an_input_reading_hash_defers_even_though_it_would_succeed(tmp_path):
    res = _resolve(cwd=tmp_path)
    assert res.deferred == ("dataset_hash",)
    assert "dataset_hash" not in res.hashes


def test_only_the_commands_declared_hashes_are_computed(tmp_path):
    narrow = CommandSpec(name=("inspect", "config"), config_sources=("default",),
                         hashes=("config_hash",))
    res = _resolve(command=narrow, cwd=tmp_path)
    assert set(res.hashes) == {"config_hash"}
    assert res.deferred == ()
    assert res.target is None


# --------------------------------------------------------------------------- #
# identity field validation
# --------------------------------------------------------------------------- #


def test_a_from_hash_field_naming_an_undeclared_hash_fails():
    class BadRuntime(RuntimeConfig):
        config_hash: Annotated[ResolvedString, FromHash("typo_hash")] = None

    class BadRoot(Root):
        runtime: BadRuntime = Field(default_factory=BadRuntime)

    with pytest.raises(DeclarationError, match="which no hash declares"):
        validate_identity_fields(BadRoot, HASHES)


def test_a_from_hash_field_that_is_authored_is_refused():
    """Assigning an authored field after its digest was computed would make the
    digest describe a configuration that never ran."""

    class Leaky(StrictModel):
        epochs: Annotated[int, FromHash("config_hash")] = 10

    class LeakyRoot(Root):
        leaky: Leaky = Field(default_factory=Leaky)

    with pytest.raises(DeclarationError, match="not resolved-only"):
        validate_identity_fields(LeakyRoot, HASHES)


def test_a_conforming_root_validates():
    assert validate_identity_fields(Root, HASHES)


# --------------------------------------------------------------------------- #
# the directory pair
# --------------------------------------------------------------------------- #


def test_both_dir_and_dir_template_is_refused(tmp_path):
    composed = {**COMPOSED, "training_outputs": {"dir": "pinned", "dir_template": "x"}}
    with pytest.raises(RefusalError, match="both authored"):
        _resolve(composed, cwd=tmp_path)


def test_a_replay_carrying_both_is_exempt(tmp_path):
    """A resolved artifact legitimately has both; the refusal catches authoring
    ambiguity, and a replay is not authored."""
    composed = {**COMPOSED, "training_outputs": {"dir": "pinned", "dir_template": "x"}}
    res = _resolve(composed, cwd=tmp_path, replay=True)
    assert res.portable.training_outputs.dir == Path("pinned")


def test_a_pinned_directory_skips_templating(tmp_path):
    composed = {**COMPOSED, "training_outputs": {"dir": "exact/place"}}
    res = _resolve(composed, cwd=tmp_path)
    assert res.portable.training_outputs.dir == Path("exact/place")


def test_an_enabled_leaf_with_no_directory_is_refused(tmp_path):
    composed = {
        **COMPOSED,
        "training_outputs": {"figures": {"enabled": True, "dir": None}},
    }
    with pytest.raises(RefusalError, match="enabled but has no directory"):
        _resolve(composed, cwd=tmp_path)


def test_a_leaf_template_puts_differing_values_in_different_leaves(tmp_path):
    composed = {
        **COMPOSED,
        "training_outputs": {
            "metrics": {"dir": None, "dir_template": "metrics/{epochs}"}
        },
    }
    a = _resolve(composed, cwd=tmp_path)
    b = _resolve({**composed, "epochs": 20}, cwd=tmp_path)
    assert a.portable.training_outputs.metrics.dir != b.portable.training_outputs.metrics.dir


# --------------------------------------------------------------------------- #
# augmenting
# --------------------------------------------------------------------------- #

EVAL = CommandSpec(
    name=("eval",),
    mode="augment",
    target=TARGET,
    config_sources=("resolved", "fork"),
    hashes=("config_hash",),
)


def test_an_augmenting_command_inherits_its_directory(tmp_path):
    existing = (tmp_path / "runs" / "reef-v3" / "earlier").resolve()
    res = _resolve(command=EVAL, cwd=tmp_path, existing_target=existing)
    assert res.target == existing
    assert res.portable.training_outputs.dir == Path("reef-v3/earlier")
    # the template is untouched, so an unchanged configuration keeps one index
    assert res.portable.training_outputs.dir_template is not None


def test_an_augmenting_command_without_a_directory_is_refused(tmp_path):
    with pytest.raises(RefusalError, match="must be supplied rather than rendered"):
        _resolve(command=EVAL, cwd=tmp_path)


def test_augmenting_twice_yields_the_same_resolved_configuration(tmp_path):
    """Which is what lets configuration indices deduplicate: a re-rendered
    timestamp would make every invocation unique."""
    existing = (tmp_path / "runs" / "reef-v3" / "earlier").resolve()
    a = _resolve(command=EVAL, cwd=tmp_path, existing_target=existing)
    b = _resolve(command=EVAL, cwd=tmp_path, existing_target=existing)
    assert a.portable.model_dump(mode="json") == b.portable.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# purity
# --------------------------------------------------------------------------- #


def test_resolution_writes_nothing(tmp_path):
    (tmp_path / "runs").mkdir()
    _resolve(cwd=tmp_path)
    assert list((tmp_path / "runs").iterdir()) == []


def test_resolution_exposes_no_write_method(tmp_path):
    res = _resolve(cwd=tmp_path)
    for name in ("step", "complete_hash", "record_artifact", "manifest", "flush"):
        assert not hasattr(res, name)


# --------------------------------------------------------------------------- #
# regressions found in the L1/L2 review pass
# --------------------------------------------------------------------------- #


def test_a_null_valued_token_is_refused_rather_than_minting_entropy(tmp_path):
    """A present-but-null field is not the invocation namespace.

    `{:coolname}` means "fresh" and `{path:coolname}` means "stable from that
    value", and the function told them apart by `value is None` — so a null field
    silently took the fresh branch and two resolutions of one identical
    configuration produced two randomly-named run directories.
    """

    Bare = RuntimeConfig  # the framework block, which binds no hash (D21)

    class RootBare(Root):
        # The realistic mistake now that the identity is opt-in: reach for the
        # template without declaring the hash that feeds it.
        runtime: Bare = Field(default_factory=Bare)

    class Templated(TrainingOutputs):
        dir_template: str | None = "{runtime.config_id}"

    class RootT(RootBare):
        training_outputs: Templated = Field(default_factory=Templated)

    composed = {
        **COMPOSED,
        "runtime": {"config_id": "{runtime.config_hash:coolname}"},
    }
    with pytest.raises(TemplateError, match="is null"):
        resolve(
            composed,
            root_type=RootT,
            command=TRAIN,
            hashes=HASHES,
            context=CONTEXT,
            cwd=tmp_path,
        )


def test_an_overridden_target_template_naming_a_dynamically_absent_hash_is_refused(tmp_path):
    """When a pure (reads_inputs=False) hash returns None, referencing its FromHash
    field in a target template is refused before rendering."""

    class OptBlock(StrictModel):
        opt_hash: Annotated[ResolvedString, FromHash("optional_hash")] = None

    class RootOptHash(Root):
        opt: OptBlock = Field(default_factory=OptBlock)

    hashes = HashRegistry(
        [
            FieldHash(name="config_hash", include=["experiment", "data", "epochs"]),
            SourceHash(name="optional_hash", build=lambda ctx: None, reads_inputs=False),
        ],
        root_type=RootOptHash,
    )
    validate_identity_fields(RootOptHash, hashes)
    cmd = CommandSpec(name=("train",), target=TARGET, hashes=("config_hash", "optional_hash"))
    composed = {
        **COMPOSED,
        "training_outputs": {"dir_template": "{opt.opt_hash:coolname}"},
    }
    with pytest.raises(RefusalError, match="unassigned"):
        resolve(
            composed,
            root_type=RootOptHash,
            command=cmd,
            hashes=hashes,
            context=CONTEXT,
            cwd=tmp_path,
        )


def test_authoring_resolved_only_field_is_refused_when_not_replay(tmp_path):
    composed = {**COMPOSED, "runtime": {"config_hash": "sha256:fake"}}
    with pytest.raises(RefusalError, match=r"resolved-only.*runtime\.config_hash"):
        _resolve(composed, cwd=tmp_path)

    # Replay accepts persisted resolved-only values cleanly.
    replayed = _resolve(composed, cwd=tmp_path, replay=True)
    assert replayed.portable.runtime.config_hash == replayed.hashes["config_hash"].value


def test_resolve_accepts_explicit_sweep_id_without_authoring_it(tmp_path):
    class SweptOutputs(TrainingOutputs):
        dir_template: str | None = "{runtime.sweep_id}/{experiment.name:slug}"

    class SweptRoot(Root):
        training_outputs: SweptOutputs = Field(default_factory=SweptOutputs)

    res = resolve(
        COMPOSED,
        root_type=SweptRoot,
        command=TRAIN,
        hashes=HashRegistry(
            [FieldHash(name="config_hash", include=["experiment", "data", "epochs"])],
            root_type=SweptRoot,
        ),
        context=CONTEXT,
        cwd=tmp_path,
        sweep_id="brave-otter",
    )
    assert res.portable.runtime.sweep_id == "brave-otter"
    assert res.portable.training_outputs.dir == Path("brave-otter/reef-v3")


def test_phase2_absolutizes_container_paths_and_respects_windows_absolute_paths(tmp_path):
    from honbu.flow.resolve import anchor

    class Item(StrictModel):
        manifest: PortablePath

    class RichData(StrictModel):
        uri: PortablePath
        win_path: PortablePath
        by_name: dict[str, Item] = Field(default_factory=dict)
        items: list[Item] = Field(default_factory=list)
        extra_files: list[PortablePath] = Field(default_factory=list)
        named_files: dict[str, PortablePath] = Field(default_factory=dict)

    class RichRoot(Root):
        data: RichData

    res = resolve(
        {
            "experiment": {"name": "reef"},
            "data": {
                "uri": "./rel/a",
                "win_path": "C:\\datasets\\coco",
                "by_name": {"k": {"manifest": "m/k.csv"}},
                "items": [{"manifest": "m/0.csv"}],
                "extra_files": ["f/one.txt", "D:/abs/two.txt"],
                "named_files": {"local": "n/a.txt", "win": "E:\\n\\b.txt"},
            },
        },
        root_type=RichRoot,
        command=TRAIN,
        hashes=HashRegistry([FieldHash(name="config_hash", include=["experiment"])]),
        context=CONTEXT,
        cwd=tmp_path,
    )
    assert res.portable.data.uri == (tmp_path / "rel/a").resolve()
    assert str(res.portable.data.win_path) == "C:\\datasets\\coco"
    assert res.portable.data.by_name["k"].manifest == (tmp_path / "m/k.csv").resolve()
    assert res.portable.data.items[0].manifest == (tmp_path / "m/0.csv").resolve()
    assert res.portable.data.extra_files[0] == (tmp_path / "f/one.txt").resolve()
    assert str(res.portable.data.extra_files[1]) == "D:/abs/two.txt"
    assert res.portable.data.named_files["local"] == (tmp_path / "n/a.txt").resolve()
    assert str(res.portable.data.named_files["win"]) == "E:\\n\\b.txt"
    assert str(anchor(Path("C:\\runs\\exp"), base=tmp_path)) == "C:\\runs\\exp"


def test_resolve_defaults_context_refuses_active_sweep_and_accepts_project_spec(tmp_path):
    project = ProjectSpec(
        root_type=Root,
        compose_spec=ComposeSpec(packaged=None),
        hashes=HASHES,
    )
    res = resolve(COMPOSED, command=TRAIN, project=project, cwd=tmp_path)
    assert res.context.extras["command"] == "train"
    assert res.hashes["config_hash"].value.startswith("sha256:")

    with pytest.raises(RefusalError, match="active 'sweep' block"):
        resolve(
            {**COMPOSED, "sweep": {"axes": {"epochs": [10, 20]}}},
            command=TRAIN,
            project=project,
            cwd=tmp_path,
        )


def test_phase7_leaf_authored_checks_warnings_cwd_relative_and_augment_rerender(
    tmp_path, monkeypatch
):
    from honbu.core import paths as paths_mod

    # 1. Schema defaults dir="metrics", caller authors dir_template without nulling dir.
    composed_tpl = {
        **COMPOSED,
        "training_outputs": {"metrics": {"dir_template": "metrics/{experiment.name}"}},
    }
    res = _resolve(composed_tpl, cwd=tmp_path)
    assert res.portable.training_outputs.metrics.dir == Path("metrics/reef v3")

    # 2. Authoring BOTH leaf dir and dir_template is refused when replay=False.
    with pytest.raises(RefusalError, match="sets both dir and dir_template"):
        _resolve(
            {
                **COMPOSED,
                "training_outputs": {
                    "metrics": {"dir": "m", "dir_template": "metrics/{epochs}"}
                },
            },
            cwd=tmp_path,
        )

    # 3. Leaf template sanitization warning is collected on resolution.warnings.
    warned = _resolve(
        {
            "experiment": {"name": "reef:v3"},
            "data": {"uri": "./datasets/coco8"},
            "training_outputs": {"metrics": {"dir_template": "m/{experiment.name}"}},
        },
        cwd=tmp_path,
    )
    assert any("reef:v3" in w for w in warned.warnings)

    # 4. Leaf cwd-relative dir is stored absolute.
    cwd_leaf = _resolve(
        {
            **COMPOSED,
            "training_outputs": {"metrics": {"dir": "./local_metrics"}},
        },
        cwd=tmp_path,
    )
    assert cwd_leaf.portable.training_outputs.metrics.dir == (tmp_path / "local_metrics").resolve()

    # 5. Augmenting --resolved replay re-renders templated leaf in command.scope
    #    unless leaf.dir was explicitly overridden.
    eval_cmd = CommandSpec(
        name=("eval",),
        mode="augment",
        target=TARGET,
        scope=("training_outputs.metrics",),
        config_sources=("resolved",),
        hashes=("config_hash",),
    )
    existing = (tmp_path / "runs" / "reef-v3" / "earlier").resolve()
    replayed_dict = {
        **COMPOSED,
        "epochs": 20,
        "training_outputs": {
            "dir": "reef-v3/earlier",
            "dir_template": "{experiment.name:slug}/{:timestamp}",
            "metrics": {"dir": "metrics/10", "dir_template": "metrics/{epochs}"},
        },
    }
    rerendered = _resolve(
        replayed_dict,
        command=eval_cmd,
        cwd=tmp_path,
        existing_target=existing,
        replay=True,
        overrides=("epochs=20",),
    )
    assert rerendered.portable.training_outputs.metrics.dir == Path("metrics/20")

    pinned = _resolve(
        {
            **replayed_dict,
            "training_outputs": {
                **replayed_dict["training_outputs"],
                "metrics": {"dir": "metrics/custom", "dir_template": "metrics/{epochs}"},
            },
        },
        command=eval_cmd,
        cwd=tmp_path,
        existing_target=existing,
        replay=True,
        overrides=("epochs=20", "training_outputs.metrics.dir=metrics/custom"),
    )
    assert pinned.portable.training_outputs.metrics.dir == Path("metrics/custom")

    # 6. Leaf path_length_refusal is enforced on Windows when long paths are disabled.
    monkeypatch.setattr(paths_mod, "is_windows", lambda: True)
    monkeypatch.setattr(paths_mod, "windows_long_paths_enabled", lambda: False)
    with pytest.raises(RefusalError, match="over the .* budget"):
        _resolve(
            {
                **COMPOSED,
                "training_outputs": {"metrics": {"dir": "x" * 200}},
            },
            cwd=tmp_path,
        )


def test_an_optionally_typed_output_block_still_anchors_to_the_output_root(tmp_path):
    """Phase 2 recognized output blocks with a bare issubclass test, which misses
    `Block | None`. The block's directory was then absolutized against the working
    directory and phase 6 read it back as a pinned literal, so output landed next
    to the shell."""

    class RootOpt(Root):
        training_outputs: TrainingOutputs | None = Field(default_factory=TrainingOutputs)

    res = resolve(
        COMPOSED,
        root_type=RootOpt,
        command=TRAIN,
        hashes=HASHES,
        context=CONTEXT,
        cwd=tmp_path,
    )
    assert res.target is not None
    assert res.target.is_relative_to((tmp_path / "runs").resolve())
    assert not res.portable.training_outputs.dir.is_absolute()
