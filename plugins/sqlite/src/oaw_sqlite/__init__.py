"""SQLite card: ordinary plugin registration and live graph-scoped tools."""
from typing import Literal
from importlib.resources import files
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, NodeResourceAction,
    NodeTypeDefinition, PackDefinition, PluginAsset, PluginDescriptor, RelationshipDefinition,
)
from . import engine
from .lifecycle import DatabaseLifecycle
from .data_source import DATA_SOURCE

PREFIX = "data.sqlite"


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "error"] = "available"
    description: str = Field(default="", max_length=2000,
        json_schema_extra={"agentReadable": True, "agentWritable": True})


class SQLitePlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.1.0", plugin_api_version="1.27",
        name="SQLite database", description="Persistent SQL databases with schema-first, scoped Agent tools.")

    def register(self, registration):
        registration.register_asset(PluginAsset(id="database", media_type="image/svg+xml",
            content=files(__package__).joinpath("assets/database.svg").read_bytes()))
        operations = {
            "inspect": (engine.Inspect, engine.inspect, "inspect_database",
                "Inspect this database before querying or writing. Lists existing tables, views and their SQL; select a table to inspect columns, indexes and foreign keys. Reuse existing entities and names. Returns schema_version for writes."),
            "query": (engine.Query, engine.query, "query_database",
                "Run one read-only SQLite query on the selected database. Inspect the schema first; use joins, filters and aggregates and bound parameters. Results are limited; use explicit ORDER BY/LIMIT for paging. No PRAGMA, ATTACH or file access."),
            "write": (engine.Write, engine.write, "write_database",
                "Apply one atomic additive SQLite statement after inspect_database. Prefer INSERT into an existing table or ALTER TABLE ADD COLUMN. Create a new table only when no existing entity fits; SQLite uses the existing main schema. Supports CREATE TABLE/INDEX/VIEW and INSERT. UPDATE, DELETE, REPLACE, triggers and destructive schema changes require an explicitly granted SQL privileged write connection. Reinspect after a schema conflict; do not invent new table names to bypass errors."),
            "admin": (engine.Write, engine.admin, "modify_database",
                "Privileged SQL for the selected database: may UPDATE/DELETE rows, DROP/RENAME objects, REPLACE, and create triggers. Use only for an intended change, inspect first, and query the affected rows before modifying them. This connection is an explicit sensitive grant. Prefer narrow WHERE clauses and safe additive write_database operations. One statement commits atomically; no external files, ATTACH, PRAGMA or transaction control."),
        }
        actions = {}
        for action, (model, handler, name, description) in operations.items():
            kind = f"{PREFIX}.{action}"
            async def invoke(context, capability, arguments, action=action):
                return await context.node_resource_action(capability, action, arguments)
            registration.register_capability(CapabilityDefinition(kind=kind, tool_name=name,
                description=description, input_schema=model.model_json_schema()), invoke)
            actions[action] = NodeResourceAction(handler, capability_kind=kind)
        registration.register_node_type(NodeTypeDefinition(
            id=PREFIX, label="SQL database", description="Persistent SQLite tables, schema and SQL queries",
            icon="database", icon_asset="database", color="#67a89b", deck_id="data", deck_label="Data", deck_icon="database",
            default_name="SQL database", default_size=(320, 240), default_status="available",
            statuses=frozenset({"available", "error"}), config_model=DatabaseConfig,
            traits=frozenset({"data.database", "data.source"}), lifecycle=DatabaseLifecycle(), resource_actions=actions,
            data_source=DATA_SOURCE,
            deletion_warning="Deleting this database permanently removes its tables and data. Canvas undo and copy do not preserve database files. Back up your profile before deleting data you need.",
            frontend={"preview": "preview", "body": "database", "workspace": "database"},
            surfaces={"preview": True, "inspector": True, "workspace": True}))
        for access, label, granted, sensitive in (
            ("read", "SQL read", ("inspect", "query"), False),
            ("edit", "SQL additive write", ("inspect", "query", "write"), False),
            ("manage", "SQL privileged write", tuple(operations), True),
        ):
            registration.register_relationship(RelationshipDefinition(
                id=f"{PREFIX}.{access}", label=label, short_label=label.removeprefix("SQL "),
                description=("Can inspect, query and add data/schema." if access == "edit" else
                    "Can inspect and query this database." if access == "read" else
                    "Can change or permanently delete data and schema in this database. Grant only when intended."),
                source_traits=frozenset({"core.agent"}), target_types=frozenset({PREFIX}),
                canvas_requires_confirmation=sensitive,
                capabilities=tuple(CapabilityGrantDefinition(f"{PREFIX}.{item}") for item in granted)))
        registration.register_pack(PackDefinition(id=f"{PREFIX}.default", name="SQL database",
            description="Persistent tables and scoped SQL tools.", cards=(PREFIX,), accent_color="#67a89b"))


def create_plugin():
    return SQLitePlugin()
