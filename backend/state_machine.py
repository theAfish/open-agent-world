"""Portable user-state definitions and explicit references to other owners.

Entity and state IDs belong to the definition. World object references are
remapped separately, so capturing a template preserves local graph IDs.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from backend.state_machine_expressions import (
    CountExpression, EventExpression, Expression, SignalId, StateExpression,
    bound_expression, expression_nodes, expression_type,
)


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00]+$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


class StateMachineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StatePosition(StateMachineModel):
    x: float = Field(default=0, ge=-1_000_000, le=1_000_000, allow_inf_nan=False)
    y: float = Field(default=0, ge=-1_000_000, le=1_000_000, allow_inf_nan=False)


class MachineState(StateMachineModel):
    id: Identifier
    label: Label
    position: StatePosition = Field(default_factory=StatePosition)

    @field_validator("id")
    @classmethod
    def reserve_wildcard(cls, value: str) -> str:
        if value == "*":
            raise ValueError("'*' is reserved for a transition's from_state wildcard")
        return value


class SystemProjection(StateMachineModel):
    """Canonical facts supplied by the owning subsystem, never user rules."""
    event: Identifier
    label: Label | None = None
    from_state: Identifier = "*"
    to_state: Identifier
    operation_id: Identifier | None = None


class SystemCommand(StateMachineModel):
    id: Identifier
    label: Label
    kind: Literal["capability", "node_action", "run"]
    operation_id: Identifier
    capability: Identifier | None = None
    action: Identifier | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    authorization: list[str] = Field(default_factory=list)
    outcomes: list[Identifier] = Field(min_length=1)


class MachineEntity(StateMachineModel):
    id: Identifier
    label: Label
    kind: Literal["card", "group", "spawn"]
    ownership: Literal["system", "user"] = "user"
    owner: Identifier | None = None
    projection: list[SystemProjection] = Field(default_factory=list, max_length=200)
    commands: list[SystemCommand] = Field(default_factory=list, max_length=100)
    card_id: Identifier | None = None
    parent_id: Identifier | None = None
    initial_state: Identifier
    states: list[MachineState] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_states(self) -> "MachineEntity":
        ids = {state.id for state in self.states}
        if len(ids) != len(self.states):
            raise ValueError(f"entity {self.id!r} contains duplicate state IDs")
        if self.initial_state not in ids:
            raise ValueError(f"entity {self.id!r} initial_state must refer to one of its states")
        if self.ownership == "user" and (self.owner or self.projection or self.commands):
            raise ValueError("Only system-owned groups can declare runtime projections or commands")
        if self.ownership == "system" and not self.owner:
            raise ValueError("System-owned groups require a subsystem owner")
        for projection in self.projection:
            if projection.to_state not in ids or projection.from_state not in ids | {"*"}:
                raise ValueError("Canonical projections must reference declared states")
        if len({command.id for command in self.commands}) != len(self.commands):
            raise ValueError("System command IDs must be unique")
        for command in self.commands:
            if not set(command.outcomes) <= ids:
                raise ValueError("System command outcomes must reference declared states")
            if command.kind == "capability" and not command.capability or command.kind == "node_action" and not command.action:
                raise ValueError("System commands must identify an existing operation")
        return self


class MachineTrigger(StateMachineModel):
    entity_id: Identifier
    event: Identifier
    capability: Identifier | None = None
    target_card_id: Identifier | None = None
    operation_id: Identifier | None = None
    state_id: Identifier | None = None


class ObjectReference(StateMachineModel):
    kind: Literal["current", "specific", "associated", "produced"] = "current"
    card_id: Identifier | None = None
    index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reference(self):
        if self.kind == "specific" and self.card_id is None:
            raise ValueError("a specific object reference requires card_id")
        return self


class MachineAction(StateMachineModel):
    id: Identifier
    kind: Literal["capability", "node_action", "run"] = "capability"
    capability: Identifier | None = None
    action: Identifier | None = None
    operation_id: Identifier | None = None
    target: ObjectReference = Field(default_factory=ObjectReference)
    caller: ObjectReference = Field(default_factory=ObjectReference)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation(self):
        if self.kind == "capability" and self.capability is None:
            raise ValueError("a capability action requires a registered capability kind")
        if self.kind == "node_action" and self.action is None:
            raise ValueError("a node action requires a registered action name")
        return self


class MachineReference(StateMachineModel):
    """A state group owned by another object, never an embedded definition."""
    entity_id: Identifier
    card_id: Identifier
    definition_version: int | None = Field(default=None, ge=1)
    state_group_id: Identifier | None = None


class MachineCondition(StateMachineModel):
    entity_id: Identifier
    state_id: Identifier


class MachineEffect(StateMachineModel):
    entity_id: Identifier
    from_state: Identifier
    to_state: Identifier


class CommandRequest(StateMachineModel):
    entity_id: Identifier
    state_id: Identifier
    command_id: Identifier
    arguments: dict[str, Any] = Field(default_factory=dict)


class TriggerSignal(StateMachineModel):
    id: SignalId
    label: Label
    match: MachineTrigger


class TriggerProgram(StateMachineModel):
    signals: list[TriggerSignal] = Field(min_length=1, max_length=16)
    expression: Expression
    window_seconds: float | None = Field(default=None, ge=1, le=86400, allow_inf_nan=False)
    reset: Literal["on_match", "manual"] = "on_match"

    @field_validator("expression", mode="before")
    @classmethod
    def limit_expression(cls, value):
        return bound_expression(value)

    @model_validator(mode="after")
    def validate_program(self) -> "TriggerProgram":
        signals = {signal.id for signal in self.signals}
        if len(signals) != len(self.signals):
            raise ValueError("trigger program signal IDs must be unique")
        if expression_type(self.expression) != "boolean":
            raise ValueError("trigger program expression must produce a boolean")
        for node in expression_nodes(self.expression):
            if isinstance(node, (CountExpression, EventExpression)) and node.signal not in signals:
                raise ValueError(f"trigger expression refers to unknown signal {node.signal!r}")
        return self


class MachineRule(StateMachineModel):
    id: Identifier
    name: Label
    enabled: bool = True
    trigger: MachineTrigger
    program: TriggerProgram | None = None
    conditions: list[MachineCondition] = Field(default_factory=list, max_length=200)
    effects: list[MachineEffect] = Field(default_factory=list, max_length=200)
    actions: list[MachineAction] = Field(default_factory=list, max_length=100)
    command: CommandRequest | None = None


class StateMachineConfig(StateMachineModel):
    version: Literal[1, 2]
    # Primary presentation group. Operational authority is declared by ownership.
    status_entity_id: Identifier | None = None
    entities: list[MachineEntity] = Field(min_length=1, max_length=200)
    rules: list[MachineRule] = Field(default_factory=list, max_length=1000)
    references: list[MachineReference] = Field(default_factory=list, max_length=200)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value):
        if type(value) is not int:
            raise ValueError("state machine version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> "StateMachineConfig":
        entities = {entity.id: entity for entity in self.entities}
        if self.status_entity_id is not None and self.status_entity_id not in entities:
            raise ValueError("card status must belong to a local state group")
        references = {reference.entity_id: reference for reference in self.references}
        if len(references) != len(self.references) or entities.keys() & references.keys():
            raise ValueError("state machine references must have unique entity IDs")
        known_entities = entities.keys() | references.keys()
        if len(entities) != len(self.entities):
            raise ValueError("state machine contains duplicate entity IDs")
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("state machine contains duplicate rule IDs")
        if sum(len(entity.states) for entity in self.entities) > 5000:
            raise ValueError("state machine may contain at most 5000 states")

        for entity in self.entities:
            if entity.parent_id is not None and entity.parent_id not in entities:
                raise ValueError(f"entity {entity.id!r} refers to an unknown parent")
        for entity in self.entities:
            ancestors = {entity.id}
            parent_id = entity.parent_id
            while parent_id is not None:
                if parent_id in ancestors:
                    raise ValueError("state machine entity hierarchy must not contain cycles")
                ancestors.add(parent_id)
                parent_id = entities[parent_id].parent_id

        states = {entity.id: {state.id for state in entity.states} for entity in self.entities}

        def require_state(entity_id: str, state_id: str) -> None:
            if entity_id in references:
                return  # Resolved against the owner's currently applied definition.
            if entity_id not in states or state_id not in states[entity_id]:
                raise ValueError(f"state machine refers to unknown state {entity_id!r}/{state_id!r}")

        for rule in self.rules:
            if not rule.effects and rule.command is None:
                raise ValueError("A rule needs a user transition or a legal system command")
            if rule.trigger.entity_id not in known_entities:
                raise ValueError(f"rule {rule.id!r} refers to an unknown trigger entity")
            if rule.trigger.state_id is not None:
                require_state(rule.trigger.entity_id, rule.trigger.state_id)
            if rule.program is not None:
                if self.version != 2:
                    raise ValueError("trigger programs require state machine version 2")
                for signal in rule.program.signals:
                    if signal.match.entity_id not in known_entities:
                        raise ValueError(f"signal {signal.id!r} refers to an unknown entity")
                    if signal.match.state_id is not None:
                        require_state(signal.match.entity_id, signal.match.state_id)
                for node in expression_nodes(rule.program.expression):
                    if isinstance(node, StateExpression):
                        require_state(node.entity_id, node.state_id)
            if len({condition.entity_id for condition in rule.conditions}) != len(rule.conditions):
                raise ValueError(f"rule {rule.id!r} contains repeated conditions for one entity")
            if len({effect.entity_id for effect in rule.effects}) != len(rule.effects):
                raise ValueError(f"rule {rule.id!r} must not transition one entity more than once")
            if len({action.id for action in rule.actions}) != len(rule.actions):
                raise ValueError(f"rule {rule.id!r} contains duplicate action IDs")
            if rule.command and any(action.id == "command:" + rule.command.command_id for action in rule.actions):
                raise ValueError("Action ID is reserved for this rule's system command")
            for condition in rule.conditions:
                require_state(condition.entity_id, condition.state_id)
            for effect in rule.effects:
                if effect.entity_id in entities and entities[effect.entity_id].ownership == "system":
                    raise ValueError("System state cannot be directly assigned; choose a registered command")
                require_state(effect.entity_id, effect.to_state)
                if effect.from_state != "*":
                    require_state(effect.entity_id, effect.from_state)
            if rule.command is not None:
                request = rule.command
                require_state(request.entity_id, request.state_id)
                if request.entity_id in entities:
                    group = entities[request.entity_id]
                    command = next((item for item in group.commands if item.id == request.command_id), None)
                    if group.ownership != "system" or command is None or request.state_id not in command.outcomes:
                        raise ValueError("System target requires a registered legal command with that possible outcome")
        return self


# Keep the portable v1/v2 graph schema; versioned persistence is host-owned.
StateMachineDefinition = StateMachineConfig


class StateMachinePresentation(StateMachineModel):
    coordinate_space: Literal["owner"] | None = None
    positions: dict[str, dict[str, StatePosition]] = Field(default_factory=dict)
    collapsed: list[str] = Field(default_factory=list)
    viewport: dict[str, float] | None = None
    viewports: dict[str, dict[str, float]] = Field(default_factory=dict)
    expanded: list[str] = Field(default_factory=list)


def remap_definition(definition: dict[str, Any], card_ids: Mapping[str, str]) -> dict[str, Any]:
    machine = deepcopy(definition)
    for entity in machine["entities"]:
        if entity.get("card_id") in card_ids:
            entity["card_id"] = card_ids[entity["card_id"]]
    for reference in machine.get("references", []):
        if reference["card_id"] in card_ids:
            reference["card_id"] = card_ids[reference["card_id"]]
            reference.pop("definition_version", None)
    for rule in machine["rules"]:
        triggers = [rule["trigger"], *(signal["match"] for signal in (rule.get("program") or {}).get("signals", []))]
        for trigger in triggers:
            if trigger.get("target_card_id") in card_ids:
                trigger["target_card_id"] = card_ids[trigger["target_card_id"]]
        for action in rule.get("actions", []):
            for key in ("caller", "target"):
                reference = action.get(key, {})
                if reference.get("card_id") in card_ids:
                    reference["card_id"] = card_ids[reference["card_id"]]
    return machine

