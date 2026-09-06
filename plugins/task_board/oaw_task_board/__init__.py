"""Task planning and dependency rules; no host-private services or UI code."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from open_agent_world.plugin_api import (
    CapabilityGrantDefinition, NodeDocumentAction, NodeDocumentDefinition,
    NodeTypeDefinition, PluginDescriptor, RelationshipDefinition, ResourceValidationError,
)

PREFIX = "oaw.tasks"

class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    status: Literal["todo", "doing", "done", "blocked"] = "todo"
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_title(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("Task title cannot be blank")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("A dependency may only appear once")
        return self

class Board(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[Task] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_graph(self):
        tasks = {t.id: t for t in self.tasks}
        if len(tasks) != len(self.tasks):
            raise ValueError("Task IDs must be unique")
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError("Dependencies must not form a cycle")
            if key in visited:
                return
            visiting.add(key)
            task = tasks[key]
            for dependency in task.depends_on:
                if dependency not in tasks:
                    raise ValueError(f"Task {task.title!r} refers to missing dependency {dependency!r}")
                visit(dependency)
            if task.status in {"doing", "done"} and any(tasks[d].status != "done" for d in task.depends_on):
                raise ValueError(f"Finish dependencies before starting or completing {task.title!r}. Reopen dependent tasks before reopening their prerequisites.")
            visiting.remove(key)
            visited.add(key)
        for key in tasks:
            visit(key)
        return self

class BoardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available"] = "available"
    description: str = Field(default="Shared tasks, clear dependencies, scoped progress updates.", max_length=1000)

class Upsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[Task] = Field(min_length=1, max_length=100)

class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(description="Task ID from read_tasks, local to this board.")
    status: Literal["todo", "doing", "done", "blocked"] = Field(description="New status: todo, doing, done, or blocked. Only doing/done require completed prerequisites.")
    note: str | None = Field(default=None, max_length=4000, description="Optional progress or blocker note. Omit to keep the existing note.")

class Remove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str

class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")

def read(value, arguments):
    Empty.model_validate(arguments)
    return value

def upsert(value, arguments):
    request = Upsert.model_validate(arguments)
    incoming = {task.id: task.model_dump() for task in request.tasks}
    if len(incoming) != len(request.tasks):
        raise ValueError("Task IDs must be unique")
    # Omitted tasks remain. Multiple dependent tasks can be created atomically.
    tasks = [incoming.pop(t["id"], t) for t in value["tasks"]]
    return {"tasks": tasks + list(incoming.values())}

def progress(value, arguments):
    request = Progress.model_validate(arguments)
    if not any(t["id"] == request.task_id for t in value["tasks"]):
        raise ValueError("Task no longer exists; read the board again")
    return {"tasks": [{**t, "status": request.status, "note": request.note if request.note is not None else t["note"]} if t["id"] == request.task_id else t for t in value["tasks"]]}

def remove(value, arguments):
    request = Remove.model_validate(arguments)
    if not any(t["id"] == request.task_id for t in value["tasks"]):
        raise ValueError("Task no longer exists")
    if any(request.task_id in t["depends_on"] for t in value["tasks"]):
        raise ValueError("Remove this task from dependent tasks before deleting it")
    return {"tasks": [t for t in value["tasks"] if t["id"] != request.task_id]}

def summarize(value):
    tasks = value["tasks"]
    done = {t["id"] for t in tasks if t["status"] == "done"}
    return {"total": len(tasks), "done": len(done), "ready_ids": [t["id"] for t in tasks if t["status"] == "todo" and set(t["depends_on"]) <= done]}

def capture(value):
    return {"tasks": [{**t, "status": "todo", "note": ""} for t in value["tasks"]]}

class TaskBoardPlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.1.0", plugin_api_version="1.2", name="Task Board", description="Shared todo lists and dependency-aware planning.")

    def register(self, registration):
        actions = {"read": (read, Empty), "upsert": (upsert, Upsert), "progress": (progress, Progress), "remove": (remove, Remove)}
        grants = {}
        documents = {}
        for action, (handler, model) in actions.items():
            kind = f"{PREFIX}.{action}"
            documents[action] = NodeDocumentAction(handler, capability_kind=kind, read_only=action == "read")
            async def invoke(context, capability, arguments, action=action):
                arguments = dict(arguments)
                revision = arguments.pop("expected_revision", None)
                if revision is not None and (type(revision) is not int or revision < 0):
                    raise ResourceValidationError("expected_revision must be a non-negative integer")
                return await context.node_document_action(capability, action, arguments, revision)
            registration.register_capability_handler(kind, invoke)
            schema = model.model_json_schema()
            if action == "upsert":
                schema["properties"]["tasks"]["description"] = 'List of task objects: {id: "step_name", title: "Do work", description: "", status: "todo|doing|done|blocked", depends_on: ["other_id"], note: ""}. Only id and title are required for new tasks. Existing tasks are replaced, so preserve all their fields when editing.'
            if action != "read":
                schema["properties"]["expected_revision"] = {"type": "integer", "minimum": 0, "description": "Latest board revision returned by read_tasks. Re-read after a conflict."}
                schema.setdefault("required", []).append("expected_revision")
            descriptions = {
                "read": "Read tasks and ready_ids from {target_name!r}. Dependencies must finish before a task starts. This board does not auto-run agents.",
                "upsert": "Create or replace tasks in {target_name!r}; omitted tasks remain. Read first and preserve existing progress when editing. Use depends_on for prerequisite task IDs.",
                "progress": "Update only task status and note in {target_name!r}. Cannot change titles or dependencies. Mark done only after the task's work is actually finished.",
                "remove": "Delete a task from {target_name!r}; remove incoming dependencies first.",
            }
            grants[action] = CapabilityGrantDefinition(kind=kind, tool_prefix={"read":"read_tasks", "upsert":"write_tasks", "progress":"update_task_progress", "remove":"delete_task"}[action], description=descriptions[action], input_schema=schema)
        registration.register_node_type(NodeTypeDefinition(
            id=PREFIX, label="Task Board", description="Plan tasks and dependencies; share only the permissions each Agent needs.", icon="workflow", color="#6c827d",
            deck_id="fields", deck_label="Fields", deck_icon="workflow", default_name="Task Board", default_size=(360, 235), default_status="available", statuses=frozenset({"available"}),
            config_model=BoardConfig, traits=frozenset({"oaw.task-board", "ui.task-board.v1"}),
            surfaces={"preview":True,"inspector":True,"workspace":True}, templateable=True,
            document=NodeDocumentDefinition(model=Board, actions=documents, summarize=summarize, capture=capture),
        ))
        for access, label, description, allowed in [
            ("read", "Read tasks", "Read tasks, dependencies and progress.", ("read",)),
            ("progress", "Update progress", "Read tasks and change status/notes; cannot edit the plan.", ("read", "progress")),
            ("edit", "Manage tasks", "Read, create, edit and delete tasks, dependencies and progress.", ("read", "progress", "upsert", "remove")),
        ]:
            registration.register_relationship(RelationshipDefinition(id=f"{PREFIX}.{access}", label=label, short_label=label.lower(), description=description,
                source_traits=frozenset({"core.agent"}), target_traits=frozenset({"oaw.task-board"}), templateable=True, capabilities=tuple(grants[a] for a in allowed)))

def create_plugin():
    return TaskBoardPlugin()
