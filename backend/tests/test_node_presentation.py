from dataclasses import replace
from itertools import combinations

import pytest
from pydantic import ValidationError

from open_agent_world.plugin_api import NodePresentation, WorkspaceLayoutMetadata
from backend.plugins import create_builtin_registry


def test_builtin_catalog_presentation_and_legacy_flags_agree():
    catalog = create_builtin_registry().catalog()
    for kind in ("conversation", "sandbox"):
        item = next(item for item in catalog.node_types if item.id == kind)
        assert item.presentation.model_dump(mode="json", exclude={"workspace_layout"}) == {
            "states": ["node", "preview", "workspace"], "initial": "workspace", "open": "workspace",
            "sizes": {}, "content_controls": "side",
        }
        assert item.surfaces == {"preview": True, "inspector": False, "workspace": True}
        assert item.presentation.workspace_layout.min_width == 280


@pytest.mark.parametrize("states,initial,target", [
    ((), "node", "node"),
    (("preview", "preview"), "preview", "preview"),
    (("node", "workspace"), "inspector", "workspace"),
    (("node", "workspace"), "node", "preview"),
    (("unknown",), "unknown", "unknown"),
])
def test_rejects_invalid_presentation(states, initial, target):
    with pytest.raises(ValidationError):
        NodePresentation(states=states, initial=initial, open=target)


def test_all_nonempty_state_subsets_and_initial_states_are_valid():
    levels = ("node", "preview", "inspector", "workspace")
    for count in range(1, 5):
        for states in combinations(levels, count):
            for initial in states:
                assert NodePresentation(states=states, initial=initial, open=states[-1]).initial == initial


def test_legacy_surfaces_are_adapted_and_explicit_presentation_takes_precedence():
    definition = create_builtin_registry().node_type("text")
    legacy = replace(definition, surfaces={"preview": False, "inspector": False, "workspace": True})
    assert legacy.catalog_item("test").presentation == NodePresentation(
        states=("node", "workspace"), initial="node", open="workspace",
    )
    declared = replace(legacy, presentation=NodePresentation(states=("inspector",), initial="inspector", open="inspector"))
    assert declared.catalog_item("test").surfaces == {"preview": False, "inspector": True, "workspace": False}


@pytest.mark.parametrize("metadata", [
    {"min_width": 0}, {"min_height": float("nan")}, {"ideal_width": float("inf")},
    {"ideal_height": 8193}, {"min_width": 400, "ideal_width": 200},
    {"min_height": 400, "ideal_height": 200}, {"role": "primary"},
])
def test_workspace_sizing_rejects_invalid_or_positional_metadata(metadata):
    with pytest.raises(ValidationError):
        WorkspaceLayoutMetadata(**metadata)


def test_optional_workspace_sizing_roundtrips_without_changing_canvas_presentation():
    legacy = NodePresentation(states=("node", "preview"), initial="node", open="preview")
    assert legacy.workspace_layout is None
    sized = NodePresentation(**{**legacy.model_dump(), "workspace_layout": {"ideal_width": 720}})
    assert NodePresentation.model_validate_json(sized.model_dump_json()) == sized
    assert sized.states == legacy.states and sized.sizes == legacy.sizes
    definition = replace(create_builtin_registry().node_type("text"), presentation=sized)
    assert definition.catalog_item("custom").presentation.workspace_layout.ideal_width == 720


def test_content_control_placement_roundtrips_and_rejects_unsupported_values():
    presentation = NodePresentation(states=('workspace',), initial='workspace', open='workspace', content_controls='bottom')
    definition = replace(create_builtin_registry().node_type('text'), presentation=presentation,
                         traits=frozenset({'ui.content-surface.v1'}))
    assert definition.catalog_item('custom').presentation.content_controls == 'bottom'
    assert NodePresentation.model_validate_json(presentation.model_dump_json()) == presentation
    with pytest.raises(ValidationError):
        NodePresentation(states=('workspace',), initial='workspace', open='workspace', content_controls='overlay')
