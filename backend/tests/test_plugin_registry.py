from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.main import create_app
from backend.persistence.database import Database
from backend.plugins import (
    CapabilityGrantDefinition,
    NodeTypeDefinition,
    RelationshipDefinition,
    create_builtin_registry,
)
from backend.services import create_services


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    query_language: str = "sql"


def test_legacy_card_type_check_is_migrated_for_plugin_ids(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('agent', 'text', 'image', 'sandbox')),
            name TEXT NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            width REAL NOT NULL CHECK (width > 0),
            height REAL NOT NULL CHECK (height > 0),
            expanded INTEGER NOT NULL DEFAULT 0 CHECK (expanded IN (0, 1)),
            config_json TEXT NOT NULL,
            chunk_x INTEGER NOT NULL,
            chunk_y INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO cards (
            id, type, name, x, y, width, height, expanded, config_json,
            chunk_x, chunk_y, created_at, updated_at
        ) VALUES (
            'existing', 'agent', 'Existing', 0, 0, 96, 96, 0, '{}',
            0, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(database_path)
    try:
        with database.locked() as migrated:
            schema = migrated.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cards'"
            ).fetchone()["sql"]
            assert "CHECK (type IN" not in schema
            assert migrated.execute(
                "SELECT type FROM cards WHERE id = 'existing'"
            ).fetchone()["type"] == "agent"
            assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        database.close()


def test_plugin_registration_drives_catalog_storage_edges_and_tools(tmp_path: Path) -> None:
    registry = create_builtin_registry()

    async def query_dataset(
        services: Any, capability: Any, values: dict[str, Any]
    ) -> dict[str, Any]:
        del services
        return {"target": capability.target_id, "query": values["query"]}

    registry.register_capability_handler("example.dataset.query", query_dataset)
    registry.register_node_type(NodeTypeDefinition(
        id="example.dataset",
        label="Dataset",
        description="Plugin-owned queryable dataset",
        icon="database",
        color="#6f7d73",
        deck_id="example.data",
        deck_label="Data",
        deck_icon="boxes",
        default_name="New Dataset",
        default_size=(320, 210),
        default_status="available",
        statuses=frozenset({"available", "indexing", "error"}),
        config_model=DatasetConfig,
        traits=frozenset({"example.queryable"}),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="example.query",
        label="Query",
        short_label="query",
        description="The agent can query this dataset.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"example.queryable"}),
        capabilities=(CapabilityGrantDefinition(
            kind="example.dataset.query",
            tool_prefix="query_dataset",
            description="Query dataset {target_name!r}.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Dataset query."}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),),
    ))

    settings = Settings.for_data_root(tmp_path / "managed")
    services = create_services(settings, plugins=registry)
    application = create_app(settings, services=services)
    try:
        with TestClient(application) as client:
            catalog = client.get("/api/catalog")
            assert catalog.status_code == 200
            assert "example.dataset" in {
                item["id"] for item in catalog.json()["node_types"]
            }
            assert "example.query" in {
                item["id"] for item in catalog.json()["relationships"]
            }

            agent = client.post("/api/nodes", json={"type": "agent"}).json()
            dataset_response = client.post(
                "/api/nodes",
                json={
                    "type": "example.dataset",
                    "config": {"query_language": "cypher", "plugin_value": 7},
                },
            )
            assert dataset_response.status_code == 201
            dataset = dataset_response.json()
            assert dataset["name"] == "New Dataset"
            assert dataset["config"]["query_language"] == "cypher"

            edge_response = client.post(
                "/api/edges",
                json={
                    "source": dataset["id"],
                    "target": agent["id"],
                    "relationship": "example.query",
                },
            )
            assert edge_response.status_code == 201
            assert edge_response.json()["source"] == agent["id"]
            assert edge_response.json()["target"] == dataset["id"]

            capability = services.capabilities.derive(agent["id"]).capabilities[0]
            assert capability.kind == "example.dataset.query"
            provider = WorldAgentCapabilityProvider(services)
            result = asyncio.run(provider.invoke_tool(
                agent["id"], capability.id, {"query": "MATCH (n) RETURN n"}
            ))
            assert result == {
                "target": dataset["id"],
                "query": "MATCH (n) RETURN n",
            }
    finally:
        services.close()
