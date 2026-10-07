from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.state_machine import StateMachineConfig, remap_definition
from backend.state_machine_preview import PreviewRequest, SimulationSession, replay_preview
from backend.tests.test_state_machine import machine


def signal(identity, event=None):
    return {"id": identity, "label": identity, "match": {"entity_id": "worker", "event": event or f"test.{identity}"}}


def count(identity, threshold=1):
    return {"op": "gte", "left": {"op": "count", "signal": identity}, "right": {"op": "number", "value": threshold}}


def configuration(expression=None, *, signals=None, reset="on_match", window=None):
    value = machine()
    value["version"] = 2
    rule = value["rules"][0]
    rule["trigger"] = {"entity_id": "worker", "event": "test.A"}
    rule["conditions"] = []
    rule["effects"] = [{"entity_id": "worker", "from_state": "*", "to_state": "done"}]
    rule["program"] = {"signals": signals or [signal("A")], "expression": expression or count("A", 3),
                       "reset": reset, "window_seconds": window}
    return value


def event(identity, key="A", *, scope="preview", time=None, **extra):
    return {"event_id": str(identity), "scope_key": scope, "time_ms": float(identity) * 1000 if time is None else time,
            "entity_id": "worker", "event": f"test.{key}", **extra}


def replay(value, events, **options):
    return replay_preview(PreviewRequest.model_validate({"machine": value, "events": events, **options}))


def test_a_and_b_are_observed_in_either_order_and_consumed_only_by_winning_rule():
    value = configuration({"op": "all", "args": [count("A"), count("B")]}, signals=[signal("A"), signal("B")])
    result = replay(value, [event(1, "B"), event(2, "A"), event(3, "B"), event(4, "A")])
    assert [step["rule_id"] for step in result["steps"]] == [None, "completed", None, "completed"]
    assert result["steps"][1]["rules"][0]["signal_counts"] == {"A": 1, "B": 1}
    assert result["scopes"]["preview"]["rules"][0]["signal_counts"] == {"A": 0, "B": 0}
    assert result["states"]["worker"] == "done"


def test_third_occurrence_triggers_and_duplicate_delivery_does_not_count():
    result = replay(configuration(), [event(1), event(1, time=0), event(2), event(3)])
    assert [step["rule_id"] for step in result["steps"]] == [None, None, None, "completed"]
    assert result["steps"][1]["duplicate"]
    assert result["steps"][1]["rules"][0]["reason"] == "duplicate_event"
    assert result["steps"][2]["rules"][0]["signal_counts"] == {"A": 2}


def test_scopes_separate_identity_counts_latches_and_machine_states():
    events = [event(1, scope="one"), event(2, scope="one"), event(1, scope="two"), event(3, scope="one")]
    result = replay(configuration(), events, scope_key="two")
    assert result["states"]["worker"] == "idle"
    assert result["last_rule_id"] is None
    assert result["scopes"]["one"]["states"]["worker"] == "done"
    assert result["scopes"]["two"]["rules"][0]["signal_counts"] == {"A": 1}
    assert not result["steps"][2]["duplicate"]


def test_window_includes_boundary_and_discards_older_occurrences():
    value = configuration(count("A", 2), window=2)
    result = replay(value, [event(1, time=0), event(2, time=2000), event(3, time=4001), event(4, time=7000)])
    assert [step["rule_id"] for step in result["steps"]] == [None, "completed", None, None]
    assert result["steps"][-1]["rules"][0]["signal_counts"] == {"A": 1}


def test_manual_rearms_only_after_false_and_unrelated_events_never_fire():
    value = configuration(count("A", 2), window=2, reset="manual")
    result = replay(value, [event(1, time=0), event(2, time=1), event(3, time=2), event(4, "unrelated", time=4000),
                            event(5, time=4001), event(6, time=4002)])
    assert [step["rule_id"] for step in result["steps"]] == [None, "completed", None, None, None, "completed"]
    assert result["steps"][2]["rules"][0]["reason"] == "latched"
    assert result["steps"][3]["rules"][0]["reason"] == "unrelated_event"
    assert result["steps"][3]["rules"][0]["latched"] is False


def test_manual_window_expiry_rearms_before_the_next_matching_event_is_counted():
    result = replay(configuration(count("A"), window=1, reset="manual"),
                    [event(1, time=0), event(2, time=1), event(3, time=2000)])
    assert [step["rule_id"] for step in result["steps"]] == ["completed", None, "completed"]


def test_window_expiry_checks_latch_without_the_incoming_event_pulse():
    expression = {"op": "any", "args": [{"op": "event", "signal": "A"}, count("A")]}
    result = replay(configuration(expression, window=1, reset="manual"),
                    [event(1, time=0), event(2, time=2000)])
    assert [step["rule_id"] for step in result["steps"]] == ["completed", "completed"]


def test_lower_priority_manual_rule_keeps_its_chance_to_fire():
    value = configuration(count("A"), reset="manual")
    second = deepcopy(value["rules"][0])
    second["id"] = "second"
    value["rules"].append(second)
    result = replay(value, [event(1), event(2), event(3)])
    assert [step["rule_id"] for step in result["steps"]] == ["completed", "second", None]
    assert result["steps"][0]["rules"][1]["reason"] == "lower_priority"
    assert result["steps"][0]["rules"][1]["latched"] is False


def test_failed_guard_does_not_consume_a_manual_rising_edge():
    value = configuration(count("A"), reset="manual")
    value["rules"][0]["conditions"] = [{"entity_id": "team", "state_id": "done"}]
    enabling = deepcopy(value["rules"][0])
    enabling.update(id="enable", conditions=[], effects=[{"entity_id": "team", "from_state": "idle", "to_state": "done"}])
    value["rules"].append(enabling)
    result = replay(value, [event(1), event(2)])
    assert result["steps"][0]["rules"][0]["reason"] == "guard_failed"
    assert [step["rule_id"] for step in result["steps"]] == ["enable", "completed"]


def test_conditions_and_all_effects_use_same_before_state_and_fail_atomically():
    value = configuration(count("A"))
    value["rules"][0]["effects"].append({"entity_id": "team", "from_state": "done", "to_state": "idle"})
    result = replay(value, [event(1)])
    assert result["states"]["worker"] == "idle"
    assert result["steps"][0]["rules"][0]["reason"] == "from_state_mismatch"
    assert result["steps"][0]["rules"][0]["signal_counts"] == {"A": 1}


def test_arithmetic_state_and_current_event_expression():
    expression = {"op": "all", "args": [
        {"op": "event", "signal": "A"}, {"op": "state", "entity_id": "team", "state_id": "idle"},
        {"op": "not", "arg": {"op": "event", "signal": "B"}},
        {"op": "eq", "left": {"op": "mod", "left": {"op": "count", "signal": "A"}, "right": {"op": "number", "value": 3}},
         "right": {"op": "number", "value": 0}},
    ]}
    result = replay(configuration(expression, signals=[signal("A"), signal("B")], reset="manual"),
                    [event(1), event(2), event(3), event(4), event(5), event(6)])
    assert [step["rule_id"] for step in result["steps"]] == [None, None, "completed", None, None, "completed"]


def test_unrelated_event_cannot_activate_negation_only_expression():
    value = configuration({"op": "not", "arg": {"op": "event", "signal": "A"}})
    result = replay(value, [event(1, "B")])
    assert result["steps"][0]["rules"][0]["expression_result"] is True
    assert result["last_rule_id"] is None


def test_runtime_arithmetic_failure_is_diagnostic_and_does_not_mutate_states():
    expr = {"op": "gt", "left": {"op": "div", "left": {"op": "number", "value": 1},
            "right": {"op": "count", "signal": "B"}}, "right": {"op": "number", "value": 0}}
    result = replay(configuration(expr, signals=[signal("A"), signal("B")]), [event(1)])
    diagnostic = result["steps"][0]["rules"][0]
    assert diagnostic["reason"] == "expression_error"
    assert diagnostic["expression_error"] == "division_by_zero"
    assert result["states"]["worker"] == "idle"


def test_legacy_v1_and_simple_v2_have_identical_event_semantics():
    value = machine()
    trigger = {**value["rules"][0]["trigger"], "event_id": "one", "scope_key": "preview", "time_ms": 0}
    first = replay(value, [trigger])
    value["version"] = 2
    assert replay(value, [trigger]) == first
    assert first["states"]["team"] == "done"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=1),
    lambda value: value["rules"][0]["program"].update(expression={"op": "number", "value": 1}),
    lambda value: value["rules"][0]["program"].update(expression={"op": "event", "signal": "missing"}),
    lambda value: value["rules"][0]["program"].update(expression={"op": "state", "entity_id": "absent", "state_id": "idle"}),
    lambda value: value["rules"][0]["program"].update(expression={"op": "eq", "left": {"op": "event", "signal": "A"}, "right": {"op": "number", "value": 1}}),
    lambda value: value["rules"][0]["program"].update(expression={"op": "all", "args": []}),
    lambda value: value["rules"][0]["program"].update(window_seconds=0.5),
    lambda value: value["rules"][0]["program"].update(window_seconds=86401),
    lambda value: value["rules"][0]["program"].update(signals=[signal("A"), signal("A")]),
    lambda value: value["rules"][0]["program"]["signals"][0].update(id="a-b"),
    lambda value: value["rules"][0]["program"]["signals"][0]["match"].update(entity_id="absent"),
    lambda value: value["rules"][0]["program"].update(expression={"op": "eval", "code": "print(1)"}),
    lambda value: value["rules"][0]["program"]["expression"]["right"].update(value=float("inf")),
    lambda value: value["rules"][0]["program"]["expression"]["right"].update(value=True),
])
def test_invalid_programs_are_rejected_before_preview_or_persistence(mutate):
    value = configuration()
    mutate(value)
    with pytest.raises(ValidationError):
        StateMachineConfig.model_validate(value)


def test_ast_depth_node_and_signal_limits():
    expression = {"op": "event", "signal": "A"}
    for _ in range(12):
        expression = {"op": "not", "arg": expression}
    with pytest.raises(ValidationError, match="128 nodes and 12 levels"):
        StateMachineConfig.model_validate(configuration(expression))
    expression = {"op": "all", "args": [count("A") for _ in range(43)]}
    with pytest.raises(ValidationError, match="128 nodes and 12 levels"):
        StateMachineConfig.model_validate(configuration(expression))
    with pytest.raises(ValidationError):
        StateMachineConfig.model_validate(configuration(signals=[signal(f"A{i}") for i in range(17)]))


def test_nonmonotonic_time_invalid_sources_and_replay_limits():
    for events in ([event(2), event(1)], [event(1, time=-1)], [event(1, time=float("nan"))],
                   [event(1, entity_id="missing")], [event(i) for i in range(201)]):
        with pytest.raises(ValidationError):
            PreviewRequest.model_validate({"machine": configuration(), "events": events})


def test_signal_world_targets_remap_but_local_ast_references_do_not():
    value = configuration()
    value["rules"][0]["program"]["signals"][0]["match"]["target_card_id"] = "document-card"
    result = remap_definition(value, {"document-card": "new-document", "A": "B"})
    rule = result["rules"][0]
    assert rule["program"]["signals"][0]["match"]["target_card_id"] == "new-document"
    assert rule["program"]["expression"] == value["rules"][0]["program"]["expression"]
    assert value["rules"][0]["program"]["signals"][0]["match"]["target_card_id"] == "document-card"


def test_preview_and_catalog_api_are_isolated_from_world_runtime_and_card_config(client):
    before = client.get("/api/world").json()
    response = client.post("/api/state-machines/preview", json={"machine": configuration(), "events": [event(1), event(2), event(3)]})
    assert response.status_code == 200, response.text
    assert response.json()["states"]["worker"] == "done"
    assert client.get("/api/world").json() == before
    catalog = client.get("/api/state-machines/events").json()["events"]
    assert all(item["runtime_bound"] == item["key"].startswith(("capability.", "execution.", "operation.", "run.", "state.", "agent.")) for item in catalog)
    assert {"run.completed", "capability.succeeded", "operation.succeeded", "custom"} <= {item["key"] for item in catalog}


def test_preview_api_rejects_malformed_traces_and_can_inspect_an_empty_scope(client):
    response = client.post("/api/state-machines/preview", json={"machine": configuration(), "events": [event(2), event(1)]})
    assert response.status_code == 422, response.text
    response = client.post("/api/state-machines/preview", json={"machine": configuration(), "scope_key": "fresh"})
    assert response.status_code == 200, response.text
    assert response.json()["scope_key"] == "fresh"
    assert response.json()["states"]["worker"] == "idle"


def test_simulation_session_does_not_mutate_the_definition():
    value = configuration()
    source = deepcopy(value)
    session = SimulationSession(value)
    for index in range(1, 4):
        session.feed(event(index))
    assert value == source


def test_program_persists_on_a_card_and_survives_both_template_remap_stages(client):
    from backend.tests.conftest import create_node

    worker = create_node(client, "agent", name="Worker")
    target = create_node(client, "text", name="Target")
    value = configuration()
    value["entities"][2]["card_id"] = worker["id"]
    value["rules"][0]["program"]["signals"][0]["match"]["target_card_id"] = target["id"]
    response = client.patch(f"/api/nodes/{worker['id']}", json={"config": {"state_machine": value}})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/state-machines/{worker['id']}").json()["definition"]["version"] == 2
    template = client.post("/api/legions", json={"name": "Program", "node_ids": [worker["id"], target["id"]]})
    assert template.status_code == 201, template.text
    instantiated = client.post(f"/api/legions/{template.json()['id']}/instances", json={})
    assert instantiated.status_code == 201, instantiated.text
    nodes = instantiated.json()["nodes"]
    new_worker = next(node for node in nodes if node["name"] == "Worker")
    new_target = next(node for node in nodes if node["name"] == "Target")
    restored = client.get(f"/api/state-machines/{new_worker['id']}").json()["definition"]
    assert next(entity for entity in restored["entities"] if entity["id"] == "worker")["card_id"] == new_worker["id"]
    assert restored["rules"][0]["program"]["signals"][0]["match"]["target_card_id"] == new_target["id"]
    assert restored["rules"][0]["program"]["expression"] == value["rules"][0]["program"]["expression"]


def test_capability_and_target_filters_only_count_the_selected_operation():
    signals = [signal("A")]
    signals[0]["match"].update(event="capability.succeeded", capability="text.edit", target_card_id="note")
    value = configuration(count("A"), signals=signals)
    result = replay(value, [event(1, event="capability.succeeded", capability="text.read", target_card_id="note"),
                            event(2, event="capability.succeeded", capability="text.edit", target_card_id="other"),
                            event(3, event="capability.succeeded", capability="text.edit", target_card_id="note")])
    assert [step["rule_id"] for step in result["steps"]] == [None, None, "completed"]


@pytest.mark.parametrize("op,expected", [("add", 2), ("sub", -8), ("mul", -15), ("div", -0.6), ("mod", -3)])
def test_numeric_algebra_including_javascript_compatible_negative_remainder(op, expected):
    expression = {"op": "eq", "left": {"op": op, "left": {"op": "number", "value": -3},
                   "right": {"op": "number", "value": 5}}, "right": {"op": "number", "value": expected}}
    assert replay(configuration(expression), [event(1)])["last_rule_id"] == "completed"


def test_only_winner_on_match_history_is_consumed():
    value = configuration(count("A"))
    value["rules"][0]["effects"][0]["from_state"] = "idle"
    other = deepcopy(value["rules"][0])
    other.update(id="other", effects=[{"entity_id": "team", "from_state": "*", "to_state": "done"}])
    value["rules"].append(other)
    result = replay(value, [event(1), event(2)])
    assert result["steps"][0]["rules"][1]["signal_counts"] == {"A": 1}
    assert result["steps"][0]["rule_ids"] == [value["rules"][0]["id"], "other"]
    assert result["steps"][1]["rules"][1]["signal_counts"] == {"A": 1}
    assert [step["rule_id"] for step in result["steps"]] == ["completed", "other"]


def test_replay_diagnostic_output_is_bounded_and_version_is_not_boolean():
    value = configuration()
    value["version"] = True
    with pytest.raises(ValidationError):
        StateMachineConfig.model_validate(value)
    value = configuration()
    value["rules"] = [{**deepcopy(value["rules"][0]), "id": f"rule{i}"} for i in range(126)]
    with pytest.raises(ValidationError, match="25000"):
        PreviewRequest.model_validate({"machine": value, "events": [event(i) for i in range(200)]})
