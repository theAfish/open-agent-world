"""A research task ledger. Execution and cancellation remain owned by OAW Runs."""
from typing import Literal
from uuid import uuid4
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from open_agent_world.task_graph import validate_task_graph
from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, NodeDocumentAction,
    NodeDocumentDefinition, NodeTypeDefinition, RelationshipDefinition,
    NodeExecutionDefinition, ExecutionPolicy, WorkItem, ScopedStateSpec,
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Task(Model):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=8000)
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["pending", "running", "review", "blocked", "done"] = "pending"
    acceptance: str = Field(default="", max_length=8000)
    result: str = Field(default="", max_length=12000)
    outputs: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_task(self):
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Task dependencies must be unique")
        if self.status == "done" and not self.result:
            raise ValueError("Record a verified result before completing a task")
        if any(not item.strip() or len(item) > 1024 for item in self.outputs):
            raise ValueError("Output references must be non-empty and at most 1024 characters")
        return self


class Plan(Model):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    goal: str = Field(default="", max_length=8000)
    session_id: str = Field(default="", max_length=200)
    tasks: list[Task] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_graph(self):
        validate_task_graph(self.tasks, active_statuses=frozenset({"running", "review", "done"}))
        return self


class Board(Model):
    plans: list[Plan] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_plans(self):
        if len({plan.id for plan in self.plans}) != len(self.plans):
            raise ValueError("Plan IDs must be unique")
        return self


class Read(Model):
    plan_id: str | None = None


class CreatePlan(Model):
    title: str = Field(min_length=1, max_length=180)
    goal: str = Field(default="", max_length=8000)
    # Accept old clients' descriptive labels, but do not advertise a namespace
    # argument. The host owns session selection; this field never controls it.
    session_id: SkipJsonSchema[str] = Field(default="", max_length=200)
    tasks: list[Task] = Field(default_factory=list, max_length=100)


class AddTask(Model):
    plan_id: str
    task: Task


class UpdateTask(Model):
    plan_id: str
    task_id: str
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=8000)
    depends_on: list[str] | None = Field(default=None, max_length=100)
    status: Literal["pending", "running", "review", "blocked", "done"] | None = None
    acceptance: str | None = Field(default=None, max_length=8000)
    result: str | None = Field(default=None, max_length=12000)
    outputs: list[str] | None = Field(default=None, max_length=40)


class RemoveTask(Model):
    plan_id: str
    task_id: str


def summary(value):
    plans = value["plans"]
    return {"plans": len(plans), "tasks": sum(len(p["tasks"]) for p in plans),
            "done": sum(t["status"] == "done" for p in plans for t in p["tasks"])}


def find_plan(value, plan_id):
    plan = next((p for p in value["plans"] if p["id"] == plan_id), None)
    if plan is None:
        raise ValueError("Research plan no longer exists")
    return plan


def read(value, arguments):
    request = Read.model_validate(arguments)
    plan = find_plan(value, request.plan_id) if request.plan_id else next(iter(reversed(value["plans"])), None)
    return {"plans": [{key: p[key] for key in ("id", "title", "session_id")} for p in value["plans"]],
            "plan": plan, "summary": summary(value)}


def create_plan(value, arguments):
    request = CreatePlan.model_validate(arguments)
    value["plans"].append({"id": uuid4().hex, **request.model_dump()})
    return value


def add_task(value, arguments):
    request = AddTask.model_validate(arguments)
    find_plan(value, request.plan_id)["tasks"].append(request.task.model_dump())
    return value


def update_task(value, arguments):
    request = UpdateTask.model_validate(arguments)
    plan = find_plan(value, request.plan_id)
    task = next((t for t in plan["tasks"] if t["id"] == request.task_id), None)
    if task is None:
        raise ValueError("Task no longer exists")
    if task["status"] == "running" and request.status == "done":
        raise ValueError("Collect execution results and verify the task before marking done")
    task.update(request.model_dump(exclude_none=True, exclude={"plan_id", "task_id"}))
    return value


def remove_task(value, arguments):
    request = RemoveTask.model_validate(arguments)
    plan = find_plan(value, request.plan_id)
    if not any(t["id"] == request.task_id for t in plan["tasks"]):
        raise ValueError("Task no longer exists")
    if any(request.task_id in t["depends_on"] for t in plan["tasks"]):
        raise ValueError("Remove dependent references before deleting this task")
    plan["tasks"] = [t for t in plan["tasks"] if t["id"] != request.task_id]
    return value


def capture(value):
    # A new Sandbox has no source outputs. Carry the plan, not completion claims.
    return {"plans": [{**plan, "session_id": "", "tasks": [
        {**task, "status": "pending", "result": "", "outputs": []}
        for task in plan["tasks"]]} for plan in value["plans"]]}


def work_id(plan, task):
    return hashlib.sha256(json.dumps([plan["id"], task["id"]]).encode()).hexdigest()


def work_items(value):
    items = []
    for plan in value["plans"]:
        by_id = {task["id"]: task for task in plan["tasks"]}
        for task in plan["tasks"]:
            dependencies = [by_id[key] for key in task["depends_on"]]
            ready = all(dep["status"] == "done" for dep in dependencies)
            contract = {"research_goal": plan["goal"], "plan_id": plan["id"],
                "task_id": task["id"], "title": task["title"], "description": task["description"],
                "acceptance_criteria": task.get("acceptance", ""),
                "verified_inputs": [{key: dep[key] for key in ("id", "result", "outputs")} for dep in dependencies]}
            items.append(WorkItem(id=work_id(plan, task),
                prompt="Execute only this assigned research task. Inspect the authorized tools and runtime first. "
                    "Report missing scientific parameters rather than inventing them. Do not edit the plan.\n"
                    + json.dumps(contract, ensure_ascii=False),
                ready=ready and task["status"] == "pending", retryable=ready and task["status"] == "blocked",
                metadata={"plan_id": plan["id"], "task_id": task["id"], "title": task["title"]}))
    return items


def apply_outcome(value, outcome):
    task = next((task for plan in value["plans"] for task in plan["tasks"] if work_id(plan, task) == outcome.item_id), None)
    if task is None:
        raise ValueError("Task was removed; its execution result is retained in the attempt history")
    task["status"] = "running" if outcome.status == "running" else "review" if outcome.status == "succeeded" else "blocked"
    task["result"] = (outcome.text or outcome.error or ("Awaiting executor results" if outcome.status == "running" else outcome.status))[:12000]
    if outcome.status == "running":
        task["outputs"] = []
    return value


def register(registration):
    actions = {}
    operations = {
        "read": (Read, read, "Read research plans and one plan's tasks. Omit plan_id for the latest plan."),
        "create_plan": (CreatePlan, create_plan, "Create a research plan with explicit tasks and dependency IDs. Use the current session ID when available."),
        "add_task": (AddTask, add_task, "Add a task to an existing research plan."),
        "update_task": (UpdateTask, update_task, "Update a task's plan, status, result or output paths. Start only after prerequisites are done. Completing requires a verified result. This records state; it does not execute or cancel a Run."),
        "remove_task": (RemoveTask, remove_task, "Remove a task with no dependent references. This does not cancel execution or delete output files."),
    }
    for operation, (model, handler, description) in operations.items():
        kind = "matcreator.tasks." + operation
        is_read = operation == "read"
        schema = model.model_json_schema()
        if not is_read:
            schema["properties"]["expected_revision"] = {"type": "integer", "minimum": 0}
            schema.setdefault("required", []).append("expected_revision")

        async def invoke(context, capability, arguments, op=operation):
            args = dict(arguments)
            revision = args.pop("expected_revision", None)
            return await context.node_document_action(capability, op, args, expected_revision=revision)

        registration.register_capability(CapabilityDefinition(kind=kind, tool_name="task_board_" + operation,
            target_parameter="board", description=description, input_schema=schema), invoke)
        actions[operation] = NodeDocumentAction(handler, capability_kind=kind, read_only=is_read, project=is_read)
    delegation_kind = "matcreator.tasks.delegate"
    # One control grant with bounded operations; host validates each operation's inputs.
    delegate_schema = {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["delegate", "wait", "collect", "stop"]},
            "item_id": {"type": "string"}, "library_id": {"type": "string"}, "agent_id": {"type": "string"},
            "request_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 0},
            "instance_id": {"type": "string"}, "instance_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
            "wait_mode": {"type": "string", "enum": ["any", "all"]},
            "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
        }, "required": ["action"], "additionalProperties": False,
    }
    async def delegate(context, capability, arguments):
        args = dict(arguments)
        action = args.pop("action")
        return await context.node_delegation_action(capability, action, args)
    registration.register_capability(CapabilityDefinition(kind=delegation_kind, tool_name="task_board_execute",
        target_parameter="board", input_schema=delegate_schema,
        description="Coordinate research Executors. collect returns the board, runnable items (item_id) and live attempts. "
            "delegate requires item_id, library_id, agent_id, expected_revision and a unique request_id; it starts asynchronously "
            "and automatically records the Run and task context. Reuse request_id only to recover the same invocation; retries use new IDs. "
            "Use Summoning list to discover Barracks/Agent IDs first. Dispatch independent items, then wait with instance_ids, "
            "wait_mode any/all and timeout_seconds (up to 60). Repeat waits while work is pending. "
            "Successful execution becomes review, not done: inspect outputs and update_task with verified evidence. "
            "stop requires instance_id. Task edits alone never stop execution."), delegate)
    registration.register_node_type(NodeTypeDefinition(
        id="matcreator.tasks", label="Research task board", description="Plan research steps, track dependencies and preserve verified results.",
        icon="workflow", color="#8ba69c", deck_id="tools", deck_label="Tools", deck_icon="boxes",
        default_name="Research tasks", default_size=(640, 520), default_status="available",
        statuses=frozenset({"available"}), config_model=Model, templateable=True,
        state=ScopedStateSpec(supportedScopes=("shared", "session"), defaultScope="session"),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        frontend={"preview": "tasks-preview", "body": "tasks", "workspace": "tasks"},
        document=NodeDocumentDefinition(model=Board, initial_value={"plans": []}, actions=actions,
            summarize=summary, capture=capture, max_size_bytes=1024 * 1024),
        execution=NodeExecutionDefinition(items=work_items, apply_outcome=apply_outcome,
            policy=lambda value: ExecutionPolicy(max_parallel=4, pause_on_failure=False),
            executor_relationship="", control_capability_kind=delegation_kind, summoning=True)))
    for suffix, names, label in (("view", ["read"], "Read tasks"), ("manage", list(operations), "Manage tasks")):
        registration.register_relationship(RelationshipDefinition(id="matcreator.tasks." + suffix,
            label=label, short_label="tasks", description="Read research tasks." if suffix == "view" else "Plan research and record task progress and evidence.",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"matcreator.tasks"}), templateable=True,
            capabilities=tuple(CapabilityGrantDefinition(kind="matcreator.tasks." + name) for name in names)
                + ((CapabilityGrantDefinition(kind=delegation_kind),) if suffix == "manage" else ())))
