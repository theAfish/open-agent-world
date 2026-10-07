"""Authoritative, isolated replay of state-machine triggers.

No world store, capability invocation, clock, or runtime event bus is involved.
The caller supplies deterministic event identity, scope, and timestamps.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, model_validator

from backend.state_machine import Identifier, MachineAction, MachineRule, MachineTrigger, StateMachineConfig, StateMachineModel
from backend.state_machine_expressions import ExpressionEvaluationError, evaluate_expression, expression_nodes


EVENT_CATALOG = [
    {"key": key, "label": label, "category": category, "outcome": outcome, "runtime_bound": False}
    for key, label, category, outcome in [
        ("run.started", "Run started", "core", "started"),
        ("run.completed", "Run completed", "core", "completed"),
        ("run.failed", "Run failed", "core", "failed"),
        ("run.cancelled", "Run cancelled", "core", "cancelled"),
        ("capability.started", "Capability started", "capability", "started"),
        ("capability.succeeded", "Capability succeeded", "capability", "succeeded"),
        ("capability.failed", "Capability failed", "capability", "failed"),
        ("state.entered", "User state entered", "state", "entered"),
        ("state.created", "Scoped value created", "state", "created"),
        ("state.updated", "Scoped value updated", "state", "updated"),
        ("state.deleted", "Scoped value deleted", "state", "deleted"),
        ("custom", "Custom event", "custom", "custom"),
    ]
]


class SimulationEvent(MachineTrigger):
    event_id: Identifier
    scope_key: Identifier = "preview"
    time_ms: float = Field(ge=0, allow_inf_nan=False)
    entity_ids: list[Identifier] = Field(default_factory=list, max_length=200)


class PreviewRequest(StateMachineModel):
    machine: StateMachineConfig
    events: list[SimulationEvent] = Field(default_factory=list, max_length=200)
    scope_key: Identifier | None = None
    states: dict[Identifier, Identifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_replay(self) -> "PreviewRequest":
        entities = {entity.id for entity in self.machine.entities} | {ref.entity_id for ref in self.machine.references}
        local_states = {entity.id: {state.id for state in entity.states} for entity in self.machine.entities}
        for entity_id, state_id in self.states.items():
            if entity_id not in entities or (entity_id in local_states and state_id not in local_states[entity_id]):
                raise ValueError("synthetic initial state must refer to a defined user state")
        times: dict[str, float] = {}
        seen: set[tuple[str, str]] = set()
        for event in self.events:
            if event.entity_id not in entities or any(entity_id not in entities for entity_id in event.entity_ids):
                raise ValueError(f"preview event refers to unknown entity {event.entity_id!r}")
            identity = event.scope_key, event.event_id
            if identity in seen:
                continue
            seen.add(identity)
            if event.time_ms < times.get(event.scope_key, 0):
                raise ValueError("preview timestamps must be nondecreasing within each scope")
            times[event.scope_key] = event.time_ms
        # Bound both diagnostic output and expression work for a single request.
        if len(self.events) * len(self.machine.rules) > 25_000:
            raise ValueError("preview supports at most 25000 event-rule observations; use a shorter trace")
        work = sum(1 + len(rule.conditions) + len(rule.effects)
                   + (len(rule.program.signals) + sum(1 for _ in expression_nodes(rule.program.expression))
                      if rule.program else 1) for rule in self.machine.rules)
        if len(self.events) * work > 2_000_000:
            raise ValueError("preview expression work exceeds the replay budget; use a shorter trace")
        return self


def trigger_matches(match: MachineTrigger, event: SimulationEvent) -> bool:
    return (match.entity_id in {event.entity_id, *event.entity_ids} and match.event == event.event
            and (match.capability is None or match.capability == event.capability)
            and (match.target_card_id is None or match.target_card_id == event.target_card_id)
            and (getattr(match, "operation_id", None) is None or match.operation_id == event.operation_id)
            and (match.state_id is None or match.state_id == event.state_id))


@dataclass
class RuleMemory:
    observations: dict[str, deque[float]] = field(default_factory=dict)
    latched: bool = False

    def counts(self) -> dict[str, int]:
        return {signal: len(times) for signal, times in self.observations.items()}


@dataclass
class ScopeMemory:
    states: dict[str, str]
    rules: dict[str, RuleMemory]
    seen: set[str] = field(default_factory=set)
    time_ms: float = 0
    last_rule_id: str | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


class TriggerEvaluator:
    """Evaluate one rule against a pre-transition state and scoped history."""

    def __init__(self, rule: MachineRule):
        self.rule = rule

    def new_memory(self) -> RuleMemory:
        return RuleMemory({signal.id: deque() for signal in self.rule.program.signals} if self.rule.program else {})

    def observe(self, memory: RuleMemory, states: dict[str, str], event: SimulationEvent, observations=None) -> dict[str, Any]:
        rule, program = self.rule, self.rule.program
        observations = observations or [event]
        matched: set[str] = set()
        expression_error = None
        if program:
            if rule.enabled:
                matched = {signal.id for signal in program.signals if any(trigger_matches(signal.match, item) for item in observations)}
            expired = False
            if program.window_seconds is not None:
                cutoff = event.time_ms - program.window_seconds * 1000
                for times in memory.observations.values():
                    while times and times[0] < cutoff:
                        times.popleft()
                        expired = True
            if expired and memory.latched:
                # Advancing the window can make a count predicate false before
                # this event makes it true again, even with no intervening event.
                try:
                    if not evaluate_expression(program.expression, memory.counts(), set(), states):
                        memory.latched = False
                except (ExpressionEvaluationError, OverflowError):
                    pass
            for signal_id in matched:
                memory.observations[signal_id].append(event.time_ms)
            try:
                result = bool(evaluate_expression(program.expression, memory.counts(), matched, states))
            except (ExpressionEvaluationError, OverflowError) as error:
                result = None
                expression_error = str(error)
            if result is False:
                memory.latched = False
            relevant = bool(matched)
        else:
            result = relevant = any(trigger_matches(rule.trigger, item) for item in observations)
        conditions_match = all(states[condition.entity_id] == condition.state_id for condition in rule.conditions)
        from_states_match = all(effect.from_state == "*" or states[effect.entity_id] == effect.from_state for effect in rule.effects)
        reason = ("disabled" if not rule.enabled else "unrelated_event" if not relevant
                  else "expression_error" if expression_error else "expression_false" if not result
                  else "guard_failed" if not conditions_match else "from_state_mismatch" if not from_states_match
                  else "latched" if program and program.reset == "manual" and memory.latched else "eligible")
        return {"rule_id": rule.id, "signal_counts": memory.counts(), "matched_signals": sorted(matched),
                "expression_result": result, "expression_error": expression_error,
                "conditions_match": conditions_match, "from_states_match": from_states_match,
                "eligible": reason == "eligible", "triggered": False, "reason": reason, "latched": memory.latched}

    def consume(self, memory: RuleMemory) -> None:
        if self.rule.program is None:
            return
        if self.rule.program.reset == "on_match":
            for times in memory.observations.values():
                times.clear()
            memory.latched = False
        else:
            memory.latched = True


class SimulationSession:
    """One disposable replay with independent state, counts, and latches per scope."""

    def __init__(self, machine: StateMachineConfig | dict[str, Any]):
        self.machine = StateMachineConfig.model_validate(machine).model_copy(deep=True)
        self.evaluators = [TriggerEvaluator(rule) for rule in self.machine.rules]
        self.scopes: dict[str, ScopeMemory] = {}
        self.steps: list[dict[str, Any]] = []

    def _scope(self, key: str) -> ScopeMemory:
        if key not in self.scopes:
            self.scopes[key] = ScopeMemory(
                {entity.id: entity.initial_state for entity in self.machine.entities},
                {evaluator.rule.id: evaluator.new_memory() for evaluator in self.evaluators})
        return self.scopes[key]

    def feed(self, event: SimulationEvent | dict[str, Any]) -> dict[str, Any]:
        event = SimulationEvent.model_validate(event)
        scope = self._scope(event.scope_key)
        if event.entity_id not in scope.states:
            raise ValueError(f"preview event refers to unknown entity {event.entity_id!r}")
        if len(self.steps) >= 200:
            raise ValueError("preview supports at most 200 events")
        duplicate = event.event_id in scope.seen
        if not duplicate and event.time_ms < scope.time_ms:
            raise ValueError("preview timestamps must be nondecreasing within each scope")
        winners, changes = [], []
        if duplicate:
            diagnostics = [{**item, "matched_signals": [], "eligible": False, "triggered": False, "reason": "duplicate_event"}
                           for item in self._diagnostics(scope)]
        else:
            scope.seen.add(event.event_id)
            scope.time_ms = event.time_ms
            owners = {group.owner for group in self.machine.entities if group.id in {event.entity_id, *event.entity_ids} and group.ownership == "system"}
            winners, diagnostics, changes = evaluate_observation(self.machine, self.evaluators, scope.rules, scope.states, event, owners=owners)
            scope.diagnostics = diagnostics
            if winners:
                scope.last_rule_id = winners[0]
        step = {"event_id": event.event_id, "scope_key": event.scope_key, "time_ms": event.time_ms,
                "states": dict(scope.states), "rule_id": winners[0] if winners else None, "rule_ids": winners,
                "system_transitions": changes, "duplicate": duplicate, "rules": diagnostics}
        step["actions"] = [action.model_dump() for rule in self.machine.rules if rule.id in winners
                           for action in rule_actions(self.machine, rule)]
        self.steps.append(step)
        return step

    def _diagnostics(self, scope: ScopeMemory) -> list[dict[str, Any]]:
        if scope.diagnostics:
            return [{**item, "signal_counts": scope.rules[item["rule_id"]].counts(),
                     "latched": scope.rules[item["rule_id"]].latched} for item in scope.diagnostics]
        return [{"rule_id": evaluator.rule.id, "signal_counts": scope.rules[evaluator.rule.id].counts(),
                 "matched_signals": [], "expression_result": None, "expression_error": None,
                 "conditions_match": None, "from_states_match": None,
                 "eligible": False, "triggered": False, "reason": "no_events", "latched": False}
                for evaluator in self.evaluators]

    def result(self, scope_key: str | None = None) -> dict[str, Any]:
        selected = scope_key or (self.steps[-1]["scope_key"] if self.steps else "preview")
        scope = self._scope(selected)
        return {"scope_key": selected, "states": dict(scope.states), "last_rule_id": scope.last_rule_id,
                "steps": self.steps,
                "scopes": {key: {"states": dict(value.states), "last_rule_id": value.last_rule_id,
                                  "rules": self._diagnostics(value)} for key, value in self.scopes.items()}}


def replay_preview(request: PreviewRequest) -> dict[str, Any]:
    session = SimulationSession(request.machine)
    for key in {request.scope_key or "preview", *(event.scope_key for event in request.events)}:
        session._scope(key).states.update(request.states)
    for event in request.events:
        session.feed(event)
    return session.result(request.scope_key)


def evaluate_rules(evaluators, memory, states, event, observations=None):
    """Shared deterministic transition semantics for preview and committed runs.

    All guards see the same authoritative pre-transition states; declaration
    order selects at most one rule per affected group. Multi-effect rules claim
    all their groups together. Commands claim their source, never their target.
    """
    diagnostics = [evaluator.observe(memory[evaluator.rule.id], states, event, observations) for evaluator in evaluators]
    winners, claimed = [], set()
    for evaluator, diagnostic in zip(evaluators, diagnostics):
        if not diagnostic["eligible"]:
            continue
        groups = {effect.entity_id for effect in evaluator.rule.effects} or {evaluator.rule.trigger.entity_id}
        if groups & claimed:
            diagnostic["reason"] = "lower_priority"
            continue
        winner = evaluator.rule.id
        winners.append(winner)
        claimed.update(groups)
        diagnostic.update(triggered=True, reason="triggered")
        states.update({effect.entity_id: effect.to_state for effect in evaluator.rule.effects})
        evaluator.consume(memory[winner])
        diagnostic["latched"] = memory[winner].latched
    return winners, diagnostics


def rule_actions(machine, rule):
    """Resolve command metadata to the existing action contract; no execution here."""
    actions = list(rule.actions)
    if rule.command:
        request = rule.command
        group = next(group for group in machine.entities if group.id == request.entity_id)
        command = next(command for command in group.commands if command.id == request.command_id)
        actions.insert(0, MachineAction(id="command:" + request.command_id, kind=command.kind,
            operation_id=command.operation_id, capability=command.capability, action=command.action,
            target={"kind": "specific", "card_id": group.card_id} if group.card_id else {"kind": "current"},
            arguments=request.arguments))
    return actions


def evaluate_observation(machine, evaluators, memory, states, event, *, owners=(), enabled=True, local_groups=None):
    """Shared ownership, projection and per-group evaluation for runtime/replay.

    Only a trusted owning-subsystem fact can project system state. All user
    guards see one snapshot, with the observed system fact and unchanged user
    states. Enter/exit are anchors on that fact, not editable canonical rules.
    """
    observations, changes = [event], []
    event_entities = {event.entity_id, *event.entity_ids}
    for group in machine.entities:
        if group.ownership != "system" or group.id not in event_entities:
            continue
        if local_groups is not None and group.id not in local_groups:
            continue
        for projection in group.projection:
            trusted = group.owner in owners or projection.operation_id is not None and projection.operation_id == event.operation_id
            if not trusted or projection.event != event.event or projection.operation_id not in {None, event.operation_id}:
                continue
            before = states[group.id]
            if projection.from_state not in {"*", before}:
                continue
            after = projection.to_state
            states[group.id] = after
            if before != after:
                changes.append({"kind": "system", "entity_id": group.id, "from_state": before, "state_id": after, "reason": event.event})
                for phase, state in (("exited", before), ("entered", after)):
                    observations.append(event.model_copy(update={"entity_id": group.id, "entity_ids": [], "event": "state." + phase, "state_id": state,
                                                                   "operation_id": None, "capability": None}))
            break
    # 'Currently in' is sampled on relevant observations, never polled as a loop.
    for group in machine.entities:
        if group.id in event_entities and not event.event.startswith("state."):
            observations.append(event.model_copy(update={"entity_id": group.id, "entity_ids": [], "event": "state.current", "state_id": states[group.id]}))
    winners, diagnostics = evaluate_rules(evaluators, memory, states, event, observations) if enabled else ([], [])
    return winners, diagnostics, changes
