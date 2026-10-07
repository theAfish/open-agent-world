"""Recognize the shipped pre-ownership Agent lifecycle, not arbitrary user graphs."""
from copy import deepcopy

from backend.agent_state_machine import AGENT_ACTIVITY_EVENTS


def consolidate_agent_lifecycle(value, system_groups, protected=()):
    """Replace intact legacy copies with the one RunManager-owned state group.

    Customized vocabularies/rules and groups assigned by another object remain
    user-owned. Only the five exact rules shipped before ownership are retired.
    """
    canonical = next((group for group in system_groups if group.get("owner") == "host.run_manager"), None)
    if canonical is None:
        return value, {}
    destinations = {item["event"]: item["to_state"] for item in canonical["projection"]}
    states = {state["id"]: state["label"] for state in canonical["states"]}
    aliases, retired = {}, set()
    for group in value["entities"]:
        identity = group["id"]
        original = identity
        while original.startswith("legacy_"):
            original = original.removeprefix("legacy_")
        if (original != canonical["id"] or identity in protected or group.get("ownership", "user") != "user"
                or group.get("label") != "Agent" or group.get("kind") != "card"
                or group.get("parent_id") or group.get("owner") or group.get("projection") or group.get("commands")
                or group.get("card_id") not in (None, canonical.get("card_id"))
                or group.get("initial_state") != canonical["initial_state"]
                or {state["id"]: state["label"] for state in group["states"]} != states):
            continue
        writes = [rule for rule in value["rules"] if any(effect["entity_id"] == identity for effect in rule.get("effects", []))]
        if len(writes) != len(destinations):
            continue
        events = set()
        for rule in writes:
            trigger = rule["trigger"]
            event = trigger["event"]
            if (event not in destinations or rule["id"] != event.removeprefix("agent.")
                    or rule["name"] != AGENT_ACTIVITY_EVENTS.get(event)
                    or {key: item for key, item in trigger.items() if item is not None} != {"entity_id": identity, "event": event}
                    or rule.get("conditions") or rule.get("actions") or rule.get("program") or rule.get("command")
                    or rule.get("effects") != [{"entity_id": identity, "from_state": "*", "to_state": destinations[event]}]):
                break
            events.add(event)
        else:
            if events == destinations.keys():
                aliases[identity] = canonical["id"]
                retired.update(rule["id"] for rule in writes)
    if not aliases:
        return value, {}

    def remap(item):
        if isinstance(item, list):
            return [remap(child) for child in item]
        if isinstance(item, dict):
            return {key: aliases.get(child, child) if key in {"entity_id", "parent_id", "status_entity_id"}
                    else deepcopy(child) if key in {"arguments", "references"} else remap(child) for key, child in item.items()}
        return item

    result = remap(value)
    result["entities"] = [group for group in result["entities"] if group["id"] not in aliases]
    result["rules"] = [rule for rule in result["rules"] if rule["id"] not in retired]
    return result, aliases


def consolidated_presentation(presentation, aliases, default):
    if not aliases:
        return presentation
    result = deepcopy(presentation)
    positions = result.setdefault("positions", {})
    for old, target in aliases.items():
        previous = positions.pop(old, None) if old != target else positions.get(old)
        if target not in positions and previous:
            positions[target] = previous
        saved = positions.get(target, {})
        defaults = default.get("positions", {}).get(target, {})
        # Replace only the old shipped vertical stack; preserve hand-arranged states.
        old_stack = {state: {"x": 140, "y": 90 + index * 150} for index, state in enumerate(defaults)}
        if not saved or saved == old_stack:
            positions[target] = deepcopy(defaults)
    result["viewports"] = {}
    return result
