"""The gate: stability, presence in the output template, and distinctness."""

from __future__ import annotations

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
    PortablePath,
    RefusalError,
    ResolvedPath,
    ResolvedString,
    RuntimeConfig,
    StrictModel,
    TemplatedIdentity,
)
from honbu.capabilities import (
    Coordinate,
    SweepBlock,
    SweepDeclaration,
    check_gate,
    check_gate_distinguishes,
    check_gate_in_output_template,
    check_gate_is_stable,
    check_targets_are_distinct,
    divergent_contexts,
    find_collisions,
    read_path,
    require_gate,
)
from honbu.flow import CommandSpec, TargetSpec, resolve


class Runtime(RuntimeConfig):
    """The opt-in of D21: a configuration-derived identity, in two lines."""

    config_hash: Annotated[ResolvedString, FromHash("config_hash")] = None
    config_id: TemplatedIdentity | None = "{runtime.config_hash:coolname}"


class Outputs(OutputBlock):
    dir: ResolvedPath = None
    dir_template: str | None = "{experiment.name:slug}/{runtime.config_id}"


class Root(StrictModel):
    experiment: ExperimentConfig
    runtime: Runtime = Field(default_factory=Runtime)
    epochs: int = 10
    lr: float = 0.1
    output_root: PortablePath = Path("./runs")
    outputs: Outputs = Field(default_factory=Outputs)
    sweep: SweepDeclaration = Field(default_factory=SweepBlock)


TARGET = TargetSpec(
    template="outputs.dir_template", resolved="outputs.dir", root="output_root"
)
TRAIN = CommandSpec(name=("train",), target=TARGET, hashes=("config_hash",))
HASHES = HashRegistry([FieldHash(name="config_hash", include=["experiment", "epochs", "lr"])])

BASE = {"experiment": {"name": "reef"}, "epochs": 10, "lr": 0.1}


def resolver(overrides: dict | None = None, *, root_type=Root, command=TRAIN):
    """A resolver bound to one configuration, parameterized only by the context."""
    composed = {**BASE, **(overrides or {})}

    def resolve_with(context):
        return resolve(
            composed,
            root_type=root_type,
            command=command,
            hashes=HASHES,
            context=context,
            cwd=Path("/tmp"),
        )

    return resolve_with


def coord(index: int, **values) -> Coordinate:
    return Coordinate(index=index, values=values, assignments=dict(values), selections={})


# --- reading the gate --------------------------------------------------------


def test_a_gate_naming_a_missing_field_is_refused_rather_than_reading_as_none():
    """Otherwise it reads None for every child — a collision, reported as one, with
    the actual mistake nowhere in the message."""
    config = resolver()(divergent_contexts()[0]).portable
    with pytest.raises(RefusalError, match="does not have"):
        read_path(config, "runtime.confg_id")


def test_a_gate_reaches_a_nested_value():
    config = resolver()(divergent_contexts()[0]).portable
    assert read_path(config, "experiment.name") == "reef"


# --- stability ---------------------------------------------------------------


def test_the_two_contexts_share_no_clock_and_no_entropy():
    first, second = divergent_contexts()
    assert first.seed != second.seed
    assert first.now_local.year != second.now_local.year
    assert first.now_utc != second.now_utc


def test_a_configuration_derived_gate_is_stable():
    value = check_gate_is_stable("runtime.config_id", resolve_with=resolver())
    assert value  # a rendered coolname, equal under both invocations


def test_a_gate_pointing_at_the_run_identity_is_refused():
    """The failure worth designing against: it looks like the strongest possible
    gate right up until two identical configurations become different experiments."""
    with pytest.raises(RefusalError, match="not stable") as caught:
        check_gate_is_stable("runtime.run_id", resolve_with=resolver())
    assert "would gate nothing while appearing to pass" in str(caught.value)


def test_a_gate_reached_through_a_timestamp_template_is_refused():
    """Checked after rendering, against the value that will actually be used."""

    class Stamped(Root):
        runtime: Runtime = Field(
            default_factory=lambda: Runtime(config_id="{:timestamp}")
        )

    with pytest.raises(RefusalError, match="not stable"):
        check_gate_is_stable(
            "runtime.config_id", resolve_with=resolver(root_type=Stamped)
        )


def test_stability_is_decided_after_rendering_not_from_the_authored_template():
    """``config_id`` is authored as a template and rendered in place; the gate sees
    the rendered value, which is what a directory will be named from."""
    resolve_with = resolver()
    rendered = resolve_with(divergent_contexts()[0]).portable.runtime.config_id
    assert "{" not in rendered
    assert check_gate_is_stable("runtime.config_id", resolve_with=resolve_with) == rendered


# --- presence in the output template -----------------------------------------


def test_a_gate_absent_from_the_output_template_is_refused():
    config = resolver()(divergent_contexts()[0]).portable
    with pytest.raises(RefusalError, match="does not appear in the child output") as c:
        check_gate_in_output_template(
            "epochs", config=config, template_field="outputs.dir_template"
        )
    assert "Add {epochs}" in str(c.value)


def test_a_gate_appearing_under_a_function_still_satisfies_the_template_rule():
    """The rule is about the path being read, not about how it is rendered.

    Which is why it is not sufficient on its own — see the target check below.
    """
    config = resolver()(divergent_contexts()[0]).portable
    check_gate_in_output_template(
        "experiment.name", config=config, template_field="outputs.dir_template"
    )


def test_a_gate_present_in_the_output_template_passes():
    config = resolver()(divergent_contexts()[0]).portable
    check_gate_in_output_template(
        "runtime.config_id", config=config, template_field="outputs.dir_template"
    )


def test_a_swept_command_naming_a_literal_directory_is_refused():
    """A literal directory would put every child in one place."""

    class Literal(Root):
        outputs: Outputs = Field(
            default_factory=lambda: Outputs(dir=Path("fixed"), dir_template=None)
        )

    config = resolver(root_type=Literal)(divergent_contexts()[0]).portable
    with pytest.raises(RefusalError, match="not set"):
        check_gate_in_output_template(
            "runtime.config_id", config=config, template_field="outputs.dir_template"
        )


# --- distinctness ------------------------------------------------------------


def test_distinct_gate_values_pass():
    coords = [coord(0, epochs=10), coord(1, epochs=20)]
    check_gate_distinguishes("runtime.config_id", ["a", "b"], coords)


def test_colliding_children_are_named_along_with_the_axes_that_failed():
    """``lr`` moves between the two children and the gate does not reflect it —
    which is exactly the diagnosis the caller needs."""
    coords = [coord(0, epochs=10, lr=0.1), coord(1, epochs=10, lr=0.2)]
    with pytest.raises(RefusalError, match="does not distinguish") as caught:
        check_gate_distinguishes("runtime.config_id", ["same", "same"], coords)
    message = str(caught.value)
    assert "#0" in message and "#1" in message
    assert "varies by ['lr']" in message
    assert "'epochs'" not in message.split("varies by")[1]


def test_the_report_names_only_the_axes_that_actually_vary():
    coords = [coord(0, a=1, b=1), coord(1, a=1, b=2)]
    (collision,) = find_collisions(["x", "x"], coords)
    assert collision.undistinguished == ("b",)
    assert collision.indices == (0, 1)


def test_many_collisions_are_summarized_rather_than_dumped():
    coords = [coord(i, n=i) for i in range(40)]
    values = [f"v{i // 2}" for i in range(40)]
    with pytest.raises(RefusalError) as caught:
        check_gate_distinguishes("g", values, coords)
    assert "and 15 more" in str(caught.value)


def test_the_refusal_suggests_both_remedies():
    coords = [coord(0, lr=0.1), coord(1, lr=0.2)]
    with pytest.raises(RefusalError) as caught:
        check_gate_distinguishes("g", ["same", "same"], coords)
    message = str(caught.value)
    assert "widen what the gate reads" in message
    assert "child index or a realized axis value" in message


def test_unhashable_gate_values_still_group():
    """A gate may legitimately land on a list or a mapping."""
    coords = [coord(0, a=1), coord(1, a=2)]
    with pytest.raises(RefusalError, match="does not distinguish"):
        check_gate_distinguishes("g", [["x", 1], ["x", 1]], coords)


# --- distinct directories, which distinctness does not imply -----------------


def test_children_resolving_to_one_directory_are_refused():
    coords = [coord(0, name="reef v3"), coord(1, name="reef-v3")]
    with pytest.raises(RefusalError, match="claimed by more than one child") as caught:
        check_targets_are_distinct(
            [Path("/runs/reef-v3"), Path("/runs/reef-v3")], coords
        )
    assert "overwrite one another" in str(caught.value)


def test_distinct_directories_pass():
    coords = [coord(0, n=1), coord(1, n=2)]
    check_targets_are_distinct([Path("/runs/a"), Path("/runs/b")], coords)


def test_a_lossy_template_function_defeats_a_sound_gate():
    """The narrow case the template rule cannot catch.

    ``{experiment.name:slug}`` maps 'reef v3' and 'reef-v3' to one segment, so both
    children carry distinct gate values, satisfy the template rule, and still land
    in one place.
    """
    coords = [coord(0, name="reef v3"), coord(1, name="reef-v3")]
    config = resolver()(divergent_contexts()[0]).portable

    # The gate itself is beyond reproach on both earlier checks.
    check_gate_in_output_template(
        "experiment.name", config=config, template_field="outputs.dir_template"
    )
    check_gate_distinguishes("experiment.name", ["reef v3", "reef-v3"], coords)

    with pytest.raises(RefusalError, match="does not preserve the distinction"):
        check_targets_are_distinct([Path("/runs/reef-v3"), Path("/runs/reef-v3")], coords)


# --- the whole check, and its ordering ---------------------------------------


def test_stability_is_reported_before_distinctness():
    """An unstable gate makes the collision report meaningless — fresh values never
    collide, so the sweep would look fine."""
    coords = [coord(0, epochs=10), coord(1, epochs=20)]
    config = resolver()(divergent_contexts()[0]).portable
    with pytest.raises(RefusalError, match="not stable"):
        check_gate(
            "runtime.run_id",
            resolve_with=resolver(),
            values=["same", "same"],
            coordinates=coords,
            config=config,
            template_field="outputs.dir_template",
        )


def test_a_sound_gate_passes_every_check():
    coords = [coord(0, epochs=10), coord(1, epochs=20)]
    config = resolver()(divergent_contexts()[0]).portable
    check_gate(
        "runtime.config_id",
        resolve_with=resolver(),
        values=["a", "b"],
        coordinates=coords,
        config=config,
        template_field="outputs.dir_template",
        targets=[Path("/runs/a"), Path("/runs/b")],
    )


def test_the_directory_check_is_reported_after_the_gate_is_known_sound():
    """Its message only makes sense once the gate itself is beyond reproach."""
    coords = [coord(0, epochs=10), coord(1, epochs=20)]
    config = resolver()(divergent_contexts()[0]).portable
    with pytest.raises(RefusalError, match="does not distinguish"):
        check_gate(
            "runtime.config_id",
            resolve_with=resolver(),
            values=["same", "same"],
            coordinates=coords,
            config=config,
            template_field="outputs.dir_template",
            targets=[Path("/runs/a"), Path("/runs/a")],
        )


# --- declaring one -----------------------------------------------------------


def test_a_sweep_without_a_gate_is_refused_with_what_a_gate_is_for():
    with pytest.raises(RefusalError, match="declares no gate") as caught:
        require_gate(SweepBlock(axes={"epochs": [10, 20]}))
    assert "sweep.gate=runtime.config_id" in str(caught.value)


def test_a_declared_gate_is_returned():
    assert require_gate(SweepBlock(gate="runtime.config_id")) == "runtime.config_id"
