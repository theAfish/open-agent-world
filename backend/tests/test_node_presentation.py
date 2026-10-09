from dataclasses import replace
from itertools import combinations

import pytest
from pydantic import ValidationError

from open_agent_world.plugin_api import NodePresentation
from backend.plugins import create_builtin_registry


def test_builtin_catalog_presentation_and_legacy_flags_agree():
    catalog = create_builtin_registry().catalog()
    for kind in ("conversation", "sandbox"):
        item = next(item for item in catalog.node_types if item.id == kind)
        assert item.presentation.model_dump(mode="json") == {
            "states": ["node", "preview", "workspace"], "initial": "workspace", "open": "workspace",
            "sizes": {},
        }
        assert item.surfaces == {"preview": True, "inspector": False, "workspace": True}


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
