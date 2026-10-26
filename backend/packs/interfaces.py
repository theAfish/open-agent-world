"""Presentation resources bound to ordinary Plugin API resource actions.

These resources contain data only. Advanced registrations keep their handlers,
state, capabilities and relationships; the editor never rewrites Python code.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import Field, model_validator

from backend.packs.factory_models import FaceDesign, FunctionDesign, Model


class CardInterface(Model):
    face: FaceDesign = Field(default_factory=FaceDesign)
    function: FunctionDesign = Field(default_factory=FunctionDesign)
    action: str = Field(default="run", pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$", max_length=128)
    result_key: str = Field(default="result", max_length=120)
    surfaces: list[Literal["preview", "body", "settings", "workspace"]] = Field(
        default_factory=lambda: ["preview", "body", "settings", "workspace"], max_length=4)

    @model_validator(mode="after")
    def unique_surfaces(self):
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError("Interface surfaces must be unique")
        return self


def apply_interfaces(registration, spec):
    """Overlay presentation after registration without replacing behavior."""
    from backend.plugins.registry import NodePresentation

    for identifier, interface in spec.designs.items():
        if identifier not in registration.nodes:
            raise ValueError(f"Design references an unregistered card: {identifier}")
        node = registration.nodes[identifier]
        actions = {interface.action, *(binding.logic_id for binding in interface.function.button_bindings or [])}
        if missing := actions - node.resource_actions.keys():
            raise ValueError(f"{identifier}: unknown bound resource actions: {', '.join(sorted(missing))}")
        face, studio = interface.face, interface.face.studio
        changes = dict(authoring=interface.model_dump(mode="json"),
                       frontend={**node.frontend, **{slot: "oaw.authoring-card" for slot in interface.surfaces}})
        # An empty surface selection preserves a custom React presentation.
        if interface.surfaces:
            changes.update(card_face={"variant": face.variant, "tone": face.tone}, color=face.color)
            if studio:
                changes["presentation"] = NodePresentation(states=tuple(studio.enabled), initial=studio.initial, open=studio.open)
                size = studio.modes[studio.initial]
                changes["default_size"] = (size.width, size.height)
            elif "workspace" in interface.surfaces:
                changes["presentation"] = NodePresentation(states=("node", "preview", "inspector", "workspace"), initial="preview", open="workspace")
        registration.nodes[identifier] = replace(node, **changes)
