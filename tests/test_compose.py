"""Composition, typed configuration loading, and search path precedence.

CLI token routing and source selection moved to opt-in ``honbu.cli`` (tested in
``tests/test_cli.py``). This file pins pure-Python composition, search paths,
typed ``load_config`` / ``parent_record``, bare-comma sweep refusal, and
open-mapping sweep override rewriting.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import Field

from honbu import (
    DeclarationError,
    ExperimentConfig,
    FieldHash,
    HashRegistry,
    HashedRuntimeConfig,
    OutputBlock,
    PortablePath,
    RefusalError,
    StrictModel,
)
from honbu.flow import (
    CommandRegistry,
    CommandSpec,
    ComposeSpec,
    Manifest,
    ProjectSpec,
    TargetSpec,
    build_search_path,
    check_overrides,
    compose_config,
    compose_file,
    force_add_sweep_overrides,
    load_config,
    open_run,
    parent_record,
    resolve,
)


# --------------------------------------------------------------------------- #
# declaration-time checks
# --------------------------------------------------------------------------- #


def test_an_augmenting_command_may_not_accept_the_default_root():
    spec = CommandSpec(
        name=("eval",),
        mode="augment",
        target=TargetSpec("a.b", "a.c", "d"),
        config_sources=("default", "resolved"),
    )
    with pytest.raises(DeclarationError, match="drop 'default' from config_sources"):
        CommandRegistry([spec])


def test_a_target_with_no_configuration_source_is_refused():
    spec = CommandSpec(
        name=("train",), target=TargetSpec("a.b", "a.c", "d"), config_sources=()
    )
    with pytest.raises(DeclarationError, match="no configuration source"):
        CommandRegistry([spec])


# --------------------------------------------------------------------------- #
# search path precedence
# --------------------------------------------------------------------------- #


def test_precedence_runs_explicit_then_local_then_packaged(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "shared").mkdir()
    spec = ComposeSpec(packaged="honbu", local="configs")
    search = build_search_path(spec, ["shared"], cwd=tmp_path)
    assert search[0].name == "shared"
    assert search[1].name == "configs"
    assert search[2].name == "honbu"


def test_a_missing_explicit_directory_is_refused(tmp_path):
    (tmp_path / "configs").mkdir()
    with pytest.raises(RefusalError, match="does not exist"):
        build_search_path(ComposeSpec(), ["nope"], cwd=tmp_path)


def test_a_missing_local_directory_falls_through_to_packaged(tmp_path):
    search = build_search_path(ComposeSpec(packaged="honbu"), cwd=tmp_path)
    assert [path.name for path in search] == ["honbu"]


def test_packaged_only_compose_spec_ignores_local_configs(tmp_path):
    (tmp_path / "configs").mkdir()
    search = build_search_path(ComposeSpec(packaged="honbu", local=None), cwd=tmp_path)
    assert [path.name for path in search] == ["honbu"]


def test_no_configuration_directory_at_all_is_refused(tmp_path):
    with pytest.raises(RefusalError, match="no configuration directory found"):
        build_search_path(ComposeSpec(packaged=None), cwd=tmp_path)


# --------------------------------------------------------------------------- #
# the Hydra call, end to end
# --------------------------------------------------------------------------- #


def _write_tree(root):
    (root / "configs" / "data").mkdir(parents=True)
    (root / "configs" / "config.yaml").write_text(
        "defaults:\n  - _self_\n  - data: local\nexperiment:\n  name: base\nepochs: 10\n"
    )
    (root / "configs" / "data" / "local.yaml").write_text("uri: ./local\n")
    (root / "configs" / "data" / "nes-hf.yaml").write_text("uri: hf://org/nes\n")
    (root / "configs" / "experiment").mkdir()
    (root / "configs" / "experiment" / "reef.yaml").write_text(
        "# @package _global_\nexperiment:\n  name: reef-v3\nepochs: 200\n"
    )


def test_composition_returns_a_plain_dict(tmp_path):
    _write_tree(tmp_path)
    composed = compose_config(ComposeSpec(packaged=None), cwd=tmp_path)
    assert composed["experiment"]["name"] == "base"
    assert composed["data"]["uri"] == "./local"
    assert "hydra" not in composed


def test_a_group_selection_swaps_the_group(tmp_path):
    _write_tree(tmp_path)
    composed = compose_config(
        ComposeSpec(packaged=None), overrides=["data=nes-hf"], cwd=tmp_path
    )
    assert composed["data"]["uri"] == "hf://org/nes"


def test_a_global_package_group_promotes_to_the_root(tmp_path):
    """Why there is no named-experiment root source: the '@package _global_' header
    does the promoting, and it is a project convention, not a framework feature."""
    _write_tree(tmp_path)
    composed = compose_config(
        ComposeSpec(packaged=None), overrides=["+experiment=reef"], cwd=tmp_path
    )
    assert composed["experiment"]["name"] == "reef-v3"
    assert composed["epochs"] == 200


def test_a_field_override_reaches_the_dict(tmp_path):
    _write_tree(tmp_path)
    composed = compose_config(
        ComposeSpec(packaged=None), overrides=["epochs=50"], cwd=tmp_path
    )
    assert composed["epochs"] == 50


def test_an_overlay_supplies_groups_the_primary_directory_lacks(tmp_path):
    _write_tree(tmp_path)
    (tmp_path / "shared" / "data").mkdir(parents=True)
    (tmp_path / "shared" / "data" / "remote.yaml").write_text("uri: /shared/place\n")
    composed = compose_config(
        ComposeSpec(packaged=None),
        extra_dirs=["shared"],
        overrides=["data=remote"],
        cwd=tmp_path,
    )
    assert composed["data"]["uri"] == "/shared/place"


def test_the_primary_directory_outranks_an_overlay_for_the_same_group(tmp_path):
    _write_tree(tmp_path)
    (tmp_path / "shared" / "data").mkdir(parents=True)
    (tmp_path / "shared" / "data" / "local.yaml").write_text("uri: /shared/place\n")
    composed = compose_config(
        ComposeSpec(packaged=None), extra_dirs=["shared"], cwd=tmp_path
    )
    assert composed["data"]["uri"] == "./local"


def test_an_overlay_carrying_the_primary_config_takes_over(tmp_path):
    _write_tree(tmp_path)
    (tmp_path / "shared" / "data").mkdir(parents=True)
    (tmp_path / "shared" / "data" / "local.yaml").write_text("uri: /shared/place\n")
    (tmp_path / "shared" / "config.yaml").write_text(
        "defaults:\n  - _self_\n  - data: local\nexperiment:\n  name: shared\nepochs: 1\n"
    )
    composed = compose_config(
        ComposeSpec(packaged=None), extra_dirs=["shared"], cwd=tmp_path
    )
    assert composed["data"]["uri"] == "/shared/place"
    assert composed["experiment"]["name"] == "shared"


def test_a_missing_primary_config_is_refused(tmp_path):
    (tmp_path / "configs").mkdir()
    with pytest.raises(RefusalError, match="no config.yaml on the configuration"):
        compose_config(ComposeSpec(packaged=None), cwd=tmp_path)


# --------------------------------------------------------------------------- #
# typed load_config and parent_record (7.3)
# --------------------------------------------------------------------------- #


def test_load_config_all_four_sources_and_fork_lineage(tmp_path):
    class Data(StrictModel):
        uri: PortablePath

    class Outputs(OutputBlock):
        dir_template: str | None = "{experiment.name}/{runtime.config_id}"

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
        data: Data
        epochs: int = 10
        output_root: PortablePath = Path("./runs")
        outputs: Outputs = Field(default_factory=Outputs)

    _write_tree(tmp_path)
    hashes = HashRegistry([FieldHash("config_hash", include=["experiment", "data", "epochs"])])
    project = ProjectSpec(
        root_type=Root,
        compose_spec=ComposeSpec(packaged=None, local="configs"),
        hashes=hashes,
    )
    cmd = CommandSpec(
        name=("train",),
        target=TargetSpec("outputs.dir_template", "outputs.dir", "output_root"),
        hashes=("config_hash",),
        fork_artifact="last_ckpt",
    )

    # default
    loaded_default = load_config("default", project=project, command=cmd, cwd=tmp_path)
    assert loaded_default.composed["epochs"] == 10
    assert loaded_default.replay is False

    res = resolve(loaded_default.composed, command=cmd, project=project, cwd=tmp_path)
    with open_run(res, composed=loaded_default.composed, project=project, argv_text="train") as run:
        run.record_artifact("last_ckpt", "checkpoints/last.pt")
        run.record_artifact("best_ckpt", "checkpoints/best.pt")
        parent_dir = run.run_dir

    # file
    custom = tmp_path / "custom.yaml"
    custom.write_text("experiment:\n  name: custom\ndata:\n  uri: ./d\nepochs: 5\n")
    loaded_file = load_config("file", custom, overrides=["epochs=7"], project=project, command=cmd)
    assert loaded_file.composed["epochs"] == 7

    # resolved
    loaded_res = load_config("resolved", parent_dir, project=project, command=cmd)
    assert loaded_res.replay is True
    assert loaded_res.prior_run == parent_dir

    # fork strips resolved-only fields and reads parent composed.yaml
    loaded_fork = load_config("fork", parent_dir, overrides=["epochs=20"], project=project, command=cmd)
    assert loaded_fork.replay is False
    assert loaded_fork.prior_run == parent_dir
    assert loaded_fork.composed["epochs"] == 20
    assert "config_hash" not in loaded_fork.composed.get("runtime", {})

    # parent_record supports default + caller-selected artifact + refuses missing/unknown
    p_default = parent_record(parent_dir, command=cmd, project=project)
    assert p_default["artifact_key"] == "last_ckpt"
    assert p_default["artifact"] == "checkpoints/last.pt"
    p_best = parent_record(parent_dir, artifact="best_ckpt", command=cmd, project=project)
    assert p_best["artifact_key"] == "best_ckpt"
    assert p_best["artifact"] == "checkpoints/best.pt"

    with pytest.raises(RefusalError, match="registered no 'nope' artifact"):
        parent_record(parent_dir, artifact="nope", project=project)
    with pytest.raises(RefusalError, match="requires a parent artifact key"):
        parent_record(parent_dir, project=project)

    # Fork creates a new run with recorded parent lineage including artifact_key
    child_res = resolve(loaded_fork.composed, command=cmd, project=project, cwd=tmp_path)
    with open_run(
        child_res,
        composed=loaded_fork.composed,
        project=project,
        parent=p_best,
    ) as child:
        assert child.run_dir != parent_dir
    lineage = Manifest.load(child.run_dir).data["parent"]
    assert lineage == p_best


# --------------------------------------------------------------------------- #
# a bare top-level comma is a sweep, and Honbu does not run compositor multirun
# --------------------------------------------------------------------------- #


def test_a_bare_top_level_comma_is_refused_pointing_at_the_sweep_block():
    with pytest.raises(RefusalError, match="multi-value sweep") as caught:
        check_overrides(["epochs=10,20"])
    assert "sweep.axes.epochs=[...]" in str(caught.value)


def test_commas_that_are_part_of_a_value_compose():
    check_overrides(["tags=[a,b]", "name='a,b'", "sweep.axes.epochs=[10,20]"])


def test_override_prefixes_are_untouched():
    check_overrides(["+new.key=5", "~old.key", "++forced=1"])


def test_a_malformed_override_is_a_refusal_not_a_traceback():
    with pytest.raises(RefusalError, match="cannot parse override"):
        check_overrides(["bad==="])


def test_setting_a_template_from_the_command_line_is_told_how_to_quote_it():
    with pytest.raises(RefusalError) as caught:
        check_overrides(["outputs.dir_template=./runs/{experiment.name}"])

    message = str(caught.value)
    assert "Honbu template" in message
    assert "outputs.dir_template='\"./runs/{experiment.name}\"'" in message


def test_an_unremarkable_parse_error_gains_no_advice():
    with pytest.raises(RefusalError) as caught:
        check_overrides(["bad==="])

    assert "Honbu template" not in str(caught.value)


def test_a_composition_error_is_a_refusal_not_a_traceback(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "config.yaml").write_text("a: 1\n")
    with pytest.raises(RefusalError, match="composition failed"):
        compose_config(
            ComposeSpec(packaged=None, local="configs"),
            overrides=["nonexistent.key=2"],
            cwd=tmp_path,
        )


# --------------------------------------------------------------------------- #
# the sweep block is an open mapping, so its keys need no addition prefix
# --------------------------------------------------------------------------- #


def sweeping(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "config.yaml").write_text(
        "epochs: 3\nsweep:\n  gate: null\n  axes: {}\n  covariant_axes: {}\n"
    )
    return ComposeSpec(packaged=None, local="configs")


def test_a_bare_sweep_override_is_rewritten_to_the_force_add_form():
    assert force_add_sweep_overrides(["sweep.axes.epochs=[10,20]"]) == [
        "++sweep.axes.epochs=[10,20]"
    ]


def test_overrides_outside_the_sweep_block_are_untouched():
    tokens = ["epochs=10", "sweeping.thing=1", "+new.key=2", "~old.key"]
    assert force_add_sweep_overrides(tokens) == tokens


def test_an_explicitly_prefixed_sweep_override_is_left_alone():
    tokens = ["~sweep.axes.epochs", "+sweep.axes.lr=[0.1]", "++sweep.gate=x"]
    assert force_add_sweep_overrides(tokens) == tokens


def test_a_new_axis_composes_without_the_caller_supplying_a_prefix(tmp_path):
    spec = sweeping(tmp_path)
    composed = compose_config(
        spec, overrides=["sweep.axes.epochs=[10,20]"], cwd=tmp_path
    )
    assert composed["sweep"]["axes"] == {"epochs": [10, 20]}


def test_a_nested_axis_key_composes_and_arrives_nested(tmp_path):
    composed = compose_config(
        sweeping(tmp_path), overrides=["sweep.axes.model.backbone=[a,b]"], cwd=tmp_path
    )
    assert composed["sweep"]["axes"] == {"model": {"backbone": ["a", "b"]}}


def test_redeclaring_an_axis_replaces_rather_than_failing_as_a_duplicate(tmp_path):
    composed = compose_config(
        sweeping(tmp_path),
        overrides=["sweep.axes.epochs=[10,20]", "sweep.axes.epochs=[30]"],
        cwd=tmp_path,
    )
    assert composed["sweep"]["axes"] == {"epochs": [30]}


def test_a_covariant_axis_composes_from_the_command_line(tmp_path):
    composed = compose_config(
        sweeping(tmp_path),
        overrides=["sweep.covariant_axes.arch=[{lr:0.1,bs:8},{lr:0.2,bs:16}]"],
        cwd=tmp_path,
    )
    assert composed["sweep"]["covariant_axes"]["arch"] == [
        {"lr": 0.1, "bs": 8},
        {"lr": 0.2, "bs": 16},
    ]


def test_a_comma_axis_inside_the_block_is_still_refused(tmp_path):
    with pytest.raises(RefusalError, match="multi-value sweep"):
        compose_config(
            sweeping(tmp_path), overrides=["sweep.axes.epochs=10,20"], cwd=tmp_path
        )


def test_compose_file_resolves_a_relative_provenance_path(tmp_path, monkeypatch):
    config = tmp_path / "run" / "config"
    config.mkdir(parents=True)
    (config / "resolved.yaml").write_text("epochs: 10\n")
    monkeypatch.chdir(tmp_path)
    assert compose_file(Path("run/config/resolved.yaml"))["epochs"] == 10


# --------------------------------------------------------------------------- #
# authored file roots inherit the project search path
# --------------------------------------------------------------------------- #


def _packaged_groups(root: Path, name: str) -> Path:
    pkg = root / name
    (pkg / "data").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.yaml").write_text(
        "defaults:\n  - _self_\n  - data: local\nexperiment:\n  name: packaged\n"
    )
    (pkg / "data" / "local.yaml").write_text("uri: ./packaged-local\n")
    (pkg / "data" / "remote.yaml").write_text("uri: s3://packaged-remote\n")
    return pkg


def _standalone_root(directory: Path, data: str = "remote") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    root = directory / "configA.yaml"
    root.write_text(
        f"defaults:\n  - _self_\n  - data: {data}\nexperiment:\n  name: standalone\n"
    )
    return root


def test_a_standalone_file_root_resolves_an_unmaterialized_packaged_group(
    tmp_path, monkeypatch
):
    _packaged_groups(tmp_path, "fileroot_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    root = _standalone_root(tmp_path / "studies")
    spec = ComposeSpec(packaged="fileroot_pkg")

    # Self-contained (no spec): the packaged group is out of reach.
    with pytest.raises(RefusalError, match="composition failed"):
        compose_file(root)

    composed = compose_file(root, compose_spec=spec, cwd=tmp_path)
    assert composed["data"]["uri"] == "s3://packaged-remote"
    assert composed["experiment"]["name"] == "standalone"

    loaded = load_config(
        "file", root, overrides=["data.uri=x"], compose_spec=spec, cwd=tmp_path
    )
    assert loaded.composed["data"]["uri"] == "x"


def test_file_root_search_order_is_own_dir_then_extra_then_local_then_packaged(
    tmp_path, monkeypatch
):
    _packaged_groups(tmp_path, "order_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    spec = ComposeSpec(packaged="order_pkg", local="configs")
    (tmp_path / "configs" / "data").mkdir(parents=True)
    (tmp_path / "configs" / "data" / "remote.yaml").write_text("uri: local-remote\n")
    (tmp_path / "overlay" / "data").mkdir(parents=True)
    root = _standalone_root(tmp_path / "studies")

    # local shadows packaged
    assert compose_file(root, compose_spec=spec, cwd=tmp_path)["data"]["uri"] == (
        "local-remote"
    )
    # an explicit extra directory outranks local
    (tmp_path / "overlay" / "data" / "remote.yaml").write_text("uri: overlay-remote\n")
    assert compose_file(
        root, compose_spec=spec, extra_dirs=["overlay"], cwd=tmp_path
    )["data"]["uri"] == "overlay-remote"
    # the file's own directory outranks everything
    (tmp_path / "studies" / "data").mkdir()
    (tmp_path / "studies" / "data" / "remote.yaml").write_text("uri: sibling-remote\n")
    assert compose_file(
        root, compose_spec=spec, extra_dirs=["overlay"], cwd=tmp_path
    )["data"]["uri"] == "sibling-remote"


def test_a_file_root_named_like_the_default_root_stays_primary(tmp_path, monkeypatch):
    _packaged_groups(tmp_path, "primary_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "studies").mkdir()
    authored = tmp_path / "studies" / "config.yaml"
    authored.write_text("defaults:\n  - _self_\n  - data: remote\nexperiment:\n  name: mine\n")
    composed = compose_file(
        authored, compose_spec=ComposeSpec(packaged="primary_pkg"), cwd=tmp_path
    )
    assert composed["experiment"]["name"] == "mine"
    assert composed["data"]["uri"] == "s3://packaged-remote"


def test_file_root_tolerates_a_spec_with_no_directories(tmp_path):
    root = tmp_path / "plain.yaml"
    root.write_text("epochs: 3\n")
    composed = compose_file(
        root, compose_spec=ComposeSpec(packaged=None, local="configs"), cwd=tmp_path
    )
    assert composed == {"epochs": 3}


def test_resolved_and_fork_snapshots_stay_self_contained(tmp_path, monkeypatch):
    """A prior run's bundle never falls back to the project's search path."""
    _packaged_groups(tmp_path, "snapshot_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    run_config = tmp_path / "runs" / "r1" / "config"
    run_config.mkdir(parents=True)
    # A (corrupted) snapshot still naming a group: resolving it through packaged
    # defaults would silently change what is replayed.
    (run_config / "resolved.yaml").write_text("defaults:\n  - data: remote\n")
    (run_config / "composed.yaml").write_text("defaults:\n  - data: remote\n")

    class Root(StrictModel):
        experiment: ExperimentConfig
        runtime: HashedRuntimeConfig = Field(default_factory=HashedRuntimeConfig)
        output_root: PortablePath = Path("./runs")
        outputs: OutputBlock = Field(default_factory=OutputBlock)

    project = ProjectSpec(
        root_type=Root,
        compose_spec=ComposeSpec(packaged="snapshot_pkg"),
        hashes=HashRegistry([FieldHash("config_hash", include=["experiment"])]),
    )
    for source in ("resolved", "fork"):
        with pytest.raises(RefusalError, match="composition failed"):
            load_config(source, tmp_path / "runs" / "r1", project=project, cwd=tmp_path)
