"""Composition: the argv grammar, configuration sources, and the search path.

Hydra itself lives in the ``flow`` extra and is not installed here, so
``compose_config`` is exercised only up to the point where it would call it.
Everything below that line — the split, the source rules, the precedence — is what
actually drifted between the two projects, and is what these tests pin.
"""

from __future__ import annotations

import pytest

from honbu import RefusalError
from honbu.errors import DeclarationError
from honbu.flow import (
    compose_config,
    CommandRegistry,
    CommandSpec,
    ComposeSpec,
    OptionSpec,
    Positional,
    TargetSpec,
    build_search_path,
    parse_invocation,
    selected_source,
    split_argv,
)

# --------------------------------------------------------------------------- #
# the argv grammar
# --------------------------------------------------------------------------- #


def test_assignments_are_overrides_and_flags_are_options():
    split = split_argv(["experiment=reef-v3", "--clobber", "training.epochs=50"])
    assert split.overrides == ("experiment=reef-v3", "training.epochs=50")
    assert split.options == {"--clobber": True}
    assert split.positionals == ()


def test_group_selection_needs_no_special_handling():
    """`data=nes-hf` and `training.epochs=50` are the same kind of token; there is
    no reserved group and no named-experiment root source."""
    split = split_argv(["data=nes-hf"])
    assert split.overrides == ("data=nes-hf",)


def test_compositor_override_prefixes_route_to_overrides():
    split = split_argv(["+new.key=1", "++forced.key=2", "~drop.key=x"])
    assert len(split.overrides) == 3


def test_an_option_value_is_not_mistaken_for_a_positional():
    split = split_argv(["--config", "my.yaml", "path/to.ckpt"])
    assert split.options == {"--config": "my.yaml"}
    assert split.positionals == ("path/to.ckpt",)


def test_an_inline_option_value_works_too():
    assert split_argv(["--config=my.yaml"]).options == {"--config": "my.yaml"}


def test_a_repeatable_option_accumulates():
    split = split_argv(["--config-dir", "a", "--config-dir", "b"])
    assert split.options == {"--config-dir": ["a", "b"]}


def test_a_non_repeatable_option_given_twice_is_refused():
    with pytest.raises(RefusalError, match="given more than once"):
        split_argv(["--config", "a", "--config", "b"])


def test_a_flag_given_a_value_is_refused():
    with pytest.raises(RefusalError, match="takes no value"):
        split_argv(["--clobber=yes"])


def test_a_value_option_without_a_value_is_refused():
    with pytest.raises(RefusalError, match="requires a value"):
        split_argv(["--config"])


def test_an_unknown_option_fails_rather_than_passing_through():
    with pytest.raises(RefusalError, match="unknown option '--nope'"):
        split_argv(["--nope"])


def test_a_project_option_is_recognized_once_declared():
    split = split_argv(
        ["--device", "cuda:0"], options=[OptionSpec("--device", takes_value=True)]
    )
    assert split.options == {"--device": "cuda:0"}


# --------------------------------------------------------------------------- #
# configuration sources
# --------------------------------------------------------------------------- #

ALL = ("default", "file", "resolved", "continue")


def test_no_source_option_falls_back_to_the_default_root():
    assert selected_source({}, accepted=ALL) == ("default", None)


def test_two_sources_are_mutually_exclusive():
    with pytest.raises(RefusalError, match="mutually exclusive"):
        selected_source(
            {"--config": "a.yaml", "--resolved": "runs/x"}, accepted=ALL
        )


def test_an_augmenting_command_cannot_compose_a_fresh_root():
    with pytest.raises(RefusalError, match="cannot compose a fresh configuration"):
        selected_source({}, accepted=("resolved", "continue"))


def test_a_source_the_command_rejects_names_what_it_accepts():
    with pytest.raises(RefusalError, match="use --resolved, --continue-from"):
        selected_source({"--config": "a.yaml"}, accepted=("resolved", "continue"))


def test_a_command_taking_no_configuration_composes_nothing():
    assert selected_source({}, accepted=()) == (None, None)


def test_a_command_taking_no_configuration_refuses_a_source_option():
    with pytest.raises(RefusalError, match="takes no configuration"):
        selected_source({"--config": "a.yaml"}, accepted=())


# --------------------------------------------------------------------------- #
# positionals, end to end through a command
# --------------------------------------------------------------------------- #

INSPECT_CHECKPOINT = CommandSpec(
    name=("inspect", "checkpoint"),
    target=None,
    config_sources=(),
    positionals=(Positional("checkpoint"),),
)

EVAL = CommandSpec(
    name=("eval",),
    mode="augment",
    target=TargetSpec("eval_outputs.dir_template", "eval_outputs.dir", "output_root"),
    config_sources=("resolved", "continue"),
)


def test_a_positional_reaches_the_command_by_name():
    parsed = parse_invocation(INSPECT_CHECKPOINT, ["runs/a/last.ckpt"])
    assert parsed.positionals == {"checkpoint": "runs/a/last.ckpt"}
    assert parsed.source is None


def test_a_missing_required_positional_is_refused():
    with pytest.raises(RefusalError, match="takes positionals: checkpoint"):
        parse_invocation(INSPECT_CHECKPOINT, [])


def test_an_extra_positional_is_refused():
    with pytest.raises(RefusalError, match="takes positionals"):
        parse_invocation(INSPECT_CHECKPOINT, ["a.ckpt", "b.ckpt"])


def test_eval_takes_a_resolved_run_and_overrides_on_top():
    parsed = parse_invocation(EVAL, ["--resolved", "runs/exp/a", "prediction.iou=0.5"])
    assert parsed.source == "resolved"
    assert parsed.source_value == "runs/exp/a"
    assert parsed.overrides == ("prediction.iou=0.5",)


def test_the_recorded_argv_text_round_trips_the_invocation():
    parsed = parse_invocation(EVAL, ["--resolved", "runs/exp/a", "prediction.iou=0.5"])
    assert parsed.argv_text == "eval --resolved runs/exp/a prediction.iou=0.5"


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


def test_a_required_positional_after_an_optional_one_is_refused():
    spec = CommandSpec(
        name=("x",),
        target=None,
        config_sources=(),
        positionals=(Positional("a", required=False), Positional("b")),
    )
    with pytest.raises(DeclarationError, match="follows an optional one"):
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
    """Hydra's semantics, verified rather than assumed: hydra.searchpath entries lose
    to the directory holding the primary config. An overlay meaning to win outright
    ships its own config.yaml, which makes it the primary."""
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
