"""RunManager's immutable Agent Execution projection.

RunManager emits aggregate facts about work. User rules may observe these
states but may only request work through the existing Run admission boundary.
"""
from enum import StrEnum


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"


AGENT_ACTIVITY_EVENTS = {
    "agent.work_started": "Work is running",
    "agent.work_waiting": "All unfinished work is waiting",
    "agent.work_finished": "All work has finished",
    "agent.runtime_failed": "Last unfinished Run failed",
    "agent.ready": "Agent runtime is ready",
}


def agent_state_machine():
    destinations = {
        "agent.work_started": AgentStatus.RUNNING,
        "agent.work_waiting": AgentStatus.WAITING,
        "agent.work_finished": AgentStatus.IDLE,
        "agent.runtime_failed": AgentStatus.ERROR,
        "agent.ready": AgentStatus.IDLE,
    }
    return {
        "version": 2, "status_entity_id": "status",
        "entities": [{"id": "status", "label": "Execution", "kind": "card",
                      "ownership": "system", "owner": "host.run_manager",
                      "initial_state": AgentStatus.IDLE,
                      "states": [{"id": status.value, "label": status.value.title(),
                                  "position": {"x": 140 + index % 2 * 220, "y": 110 + index // 2 * 210}}
                                 for index, status in enumerate(AgentStatus)],
                      "projection": [{"event": event, "label": AGENT_ACTIVITY_EVENTS[event], "to_state": target.value} for event, target in destinations.items()],
                      "commands": [{"id": "start_work", "label": "Start work", "kind": "run",
                                    "operation_id": "host:run", "outcomes": ["running"],
                                    "authorization": ["RunManager admission", "agent.communicate for another Agent"],
                                    "input_schema": {"type": "object", "properties": {"prompt": {"type": "string", "minLength": 1}}, "required": ["prompt"]}}]}],
        "rules": [],
    }


def legion_state_machine():
    return {"version": 2, "status_entity_id": "status", "entities": [
        {"id": "status", "label": "Legion", "kind": "group", "initial_state": "available",
         "states": [{"id": "available", "label": "Available"}]}], "rules": []}
