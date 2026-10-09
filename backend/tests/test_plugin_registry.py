from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.main import create_app
from backend.errors import (
    GraphValidationError,
    PluginCompatibilityError,
    PluginUnavailableError,
)
from backend.persistence.database import Database
from backend.plugins import (
    PLUGIN_API_VERSION,
    CapabilityGrantDefinition,
    NodeTypeDefinition,
    PluginDefinition,
    PluginDescriptor,
    PluginRegistration,
    RelationshipDefinition,
    create_builtin_registry,
)
from backend.services import create_services


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    query_language: str = "sql"


def test_public_plugin_api_exports_canonical_contracts() -> None:
    from open_agent_world.plugin_api import (
        NodeLifecycleTransaction as PublicLifecycleTransaction,
        NodeTypeDefinition as PublicNodeTypeDefinition,
        PluginRegistry as PublicPluginRegistry,
    )

    from backend.plugins import NodeLifecycleTransaction

    assert PublicNodeTypeDefinition is NodeTypeDefinition
    assert PublicPluginRegistry is type(create_builtin_registry())
    assert PublicLifecycleTransaction is NodeLifecycleTransaction


def test_data_consumer_declares_its_own_config_fields() -> None:
    from dataclasses import replace
    from open_agent_world.plugin_api import NodeDataConsumer, PluginRegistry

    class ReaderConfig(BaseModel):
        provider_ref: str = ""
        dataset_key: str = ""

    consumer = NodeDataConsumer(source_field="provider_ref", schema_field="dataset_key", kinds=("table",))
    node = replace(create_builtin_registry().node_type("text"), id="example.reader",
                   config_model=ReaderConfig, data_consumer=consumer)
    registry = PluginRegistry()
    registry.install(PluginDefinition(PluginDescriptor(id="example.reader", version="1", plugin_api_version="1.27"),
                                     lambda registration: registration.register_node_type(node)))
    assert registry.catalog().node_types[0].data_consumer == consumer
    invalid = replace(node, id="example.invalid", data_consumer=consumer.model_copy(update={"schema_field": "missing"}))
    with pytest.raises(ValueError, match="string config fields"):
        registry.install(PluginDefinition(PluginDescriptor(id="example.invalid", version="1", plugin_api_version="1.27"),
                                         lambda registration: registration.register_node_type(invalid)))
    assert not registry.has_plugin("example.invalid")


def test_plugin_install_is_compatible_owned_and_atomic() -> None:
    registry = create_builtin_registry()

    incompatible = PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.incompatible",
            version="1.0.0",
            plugin_api_version="99.0",
        ),
        configure=lambda registration: None,
    )
    with pytest.raises(PluginCompatibilityError, match=f"host provides '{PLUGIN_API_VERSION}'"):
        registry.install(incompatible)
    assert not registry.has_plugin("example.incompatible")

    legacy = PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.legacy",
            version="1.0.0",
            plugin_api_version="1.0",
        ),
        configure=lambda registration: None,
    )
    registry.install(legacy)
    assert registry.has_plugin("example.legacy")

    major, minor = map(int, PLUGIN_API_VERSION.split("."))
    future_version = f"{major}.{minor + 1}"
    future_minor = PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.future",
            version="1.0.0",
            plugin_api_version=future_version,
        ),
        configure=lambda registration: None,
    )
    with pytest.raises(PluginCompatibilityError, match=f"requires Plugin API '{future_version}'"):
        registry.install(future_minor)

    def fail_after_staging(registration: PluginRegistration) -> None:
        registration.register_node_type(NodeTypeDefinition(
            id="example.partial",
            label="Partial",
            description="Must never become visible",
            icon="box",
            color="#777777",
            deck_id="example.test",
            deck_label="Test",
            deck_icon="boxes",
            default_name="Partial",
            default_size=(100, 100),
            default_status="available",
            statuses=frozenset({"available"}),
            config_model=DatasetConfig,
        ))
        raise RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        registry.install(PluginDefinition(
            descriptor=PluginDescriptor(
                id="example.partial-plugin",
                version="1.0.0",
                plugin_api_version=PLUGIN_API_VERSION,
            ),
            configure=fail_after_staging,
        ))
    assert not registry.has_plugin("example.partial-plugin")
    with pytest.raises(GraphValidationError, match="not registered"):
        registry.node_type("example.partial")


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

    def configure(registration: PluginRegistration) -> None:
        registration.register_capability_handler(
            "example.dataset.query", query_dataset
        )
        registration.register_node_type(NodeTypeDefinition(
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
        registration.register_relationship(RelationshipDefinition(
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

    registry.install(PluginDefinition(
        descriptor=PluginDescriptor(
            id="example.dataset",
            version="1.0.0",
            plugin_api_version=PLUGIN_API_VERSION,
        ),
        configure=configure,
    ))
    assert registry.capability_handler_owner_id(
        "example.dataset.query"
    ) == "example.dataset"

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
            assert {
                item["plugin_id"] for item in catalog.json()["node_types"]
                if item["id"] == "example.dataset"
            } == {"example.dataset"}
            assert next(
                item for item in catalog.json()["node_types"]
                if item["id"] == "example.dataset"
            )["user_creatable"] is True

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
            with services.database.locked() as connection:
                assert connection.execute(
                    "SELECT plugin_id FROM cards WHERE id = ?", (dataset["id"],)
                ).fetchone()["plugin_id"] == "example.dataset"
                assert connection.execute(
                    "SELECT plugin_id FROM edges WHERE id = ?",
                    (edge_response.json()["id"],),
                ).fetchone()["plugin_id"] == "example.dataset"
    finally:
        services.close()

    missing_services = create_services(settings, plugins=create_builtin_registry())
    try:
        with TestClient(create_app(settings, services=missing_services)) as client:
            snapshot = client.get("/api/world")
            assert snapshot.status_code == 200, snapshot.text
            missing = missing_services.world.get_card(dataset["id"])
            assert missing.missing_plugin.plugin_id == "example.dataset"
            assert missing.config["plugin_value"] == 7
            assert missing_services.capabilities.derive(agent["id"]).capabilities == []
            response = client.patch(f"/api/nodes/{dataset['id']}", json={"name": "Retained dataset", "position": {"x": 123, "y": 456}})
            assert response.status_code == 200, response.text
            assert response.json()["missing_plugin"]["reason"] == "plugin_missing"
            response = client.patch(f"/api/nodes/{dataset['id']}", json={"config": {"plugin_value": 9}})
            assert response.status_code == 422, response.text
    finally:
        missing_services.close()
    restored = create_services(settings, plugins=registry)
    try:
        with TestClient(create_app(settings, services=restored)):
            card = restored.world.get_card(dataset["id"])
            assert card.missing_plugin is None
            assert card.name == "Retained dataset"
            assert card.config["plugin_value"] == 7
            assert card.position.x == 123
            assert len(restored.capabilities.derive(agent["id"]).capabilities) == 1
    finally:
        restored.close()
    missing_services = create_services(settings, plugins=create_builtin_registry())
    try:
        with TestClient(create_app(settings, services=missing_services)) as client:
            response = client.delete(f"/api/nodes/{dataset['id']}")
            assert response.status_code == 200, response.text
            assert missing_services.world.list_edges() == []
    finally:
        missing_services.close()
