"""The Pack registers against the public API with neither SQL nor MKB installed."""
from pathlib import Path

from open_agent_world.plugin_api import PluginRegistry


def test_standalone_registration_includes_offline_docs_and_icons(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    from oaw_visualization import create_plugin

    registry = PluginRegistry()
    registry.install(create_plugin())
    catalog = registry.catalog().model_dump(mode="json")
    assert {item["id"] for item in catalog["plugins"]} == {"data.visualization"}
    assert len(catalog["node_types"]) == 5
    assert catalog["packs"][0]["tutorials"][0]["steps"][1]["id"] == "connect-schema"
    for card in catalog["node_types"]:
        assert card["icon_url"].startswith("/api/plugins/data.visualization/assets/chart-")
        assert card["data_consumer"]["schema_field"] == "schema_id"
        document = card["tutorials"][0]["document"]
        assert "Cancelling creates no connection" in document["en"]
        assert "取消不会留下连线" in document["zh-CN"]


def test_no_provider_or_private_host_imports():
    import ast
    root = Path(__file__).resolve().parents[1]
    for path in (root / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            assert not any(name.split(".")[0] in {"backend", "oaw_sqlite", "oaw_knowledge_base", "mkb"}
                           for name in modules), str(path)
