"""POC commands use real profile storage and the existing portable Legion contract."""
import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from backend import development, poc
from backend.application import write_setting
from backend.conversations.models import ConversationSessionCreate
from backend.errors import ConflictError, DomainError, PluginCompatibilityError
from backend.plugins.builtin import create_builtin_registry
from backend.services import ApplicationServices, create_services
from backend.storage_location import file_lock
from backend.world.models import CardCreate, CardPatch, EdgeCreate


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(poc, "create_services", lambda settings, **kwargs:
        create_services(settings, plugins=kwargs.get("plugins") or create_builtin_registry()))

    async def no_startup(self):
        raise AssertionError("Offline POC commands must not resume jobs or conversations")

    monkeypatch.setattr(ApplicationServices, "startup", no_startup)


def init(services):
    return asyncio.run(poc.apply_definition(services, preset="assistant", initialize=True))["node_ids"]


def test_init_reuses_profile_and_apply_preserves_edited_instances(tmp_path):
    with poc.open_profile("poc", initialize=True) as services:
        ids = init(services)
        services.world.update_card(ids["agent"], CardPatch(name="My edited agent"))
        with services.database.transaction() as db:
            write_setting(db, "poc-test-model-setting", {"keep": "local-only"})
        path = tmp_path / "legion.json"
        asyncio.run(poc.save_definition(services, ids["group"], path))
    with poc.open_profile("poc", initialize=True) as services:
        # Even an absent new source must not make init replace the current work.
        result = asyncio.run(poc.apply_definition(services, preset="not.installed", initialize=True))
        assert result["status"] == "reused"
        before = services.world.list_cards()
        updated = asyncio.run(poc.apply_definition(services, template=path))
        assert updated["node_ids"]["group"] != ids["group"]
        assert [services.world.get_card(card.id) for card in before] == before
        new_group = next(c for c in services.world.list_cards() if c.id not in {n.id for n in before} and c.type == "legion")
        assert new_group.position.x > max(c.position.x + c.size.width for c in before if not c.parent_id)
        with services.database.locked() as db:
            assert json.loads(db.execute("SELECT value_json FROM application_settings WHERE key='poc-test-model-setting'").fetchone()[0]) == {"keep": "local-only"}


def test_save_roundtrip_layout_explicit_content_and_private_state(tmp_path):
    path = tmp_path / "legion.json"
    with poc.open_profile("author", initialize=True) as services:
        ids = init(services)
        services.world.update_card(ids["agent"], CardPatch(config={"api_key": "DO-NOT-SHARE", "model": "private-profile"}))
        note = asyncio.run(services.create_card(CardCreate(type="text", name="Brief", parent_id=ids["group"], content="Example task")))
        services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Private customer session"))
        asyncio.run(poc.save_definition(services, ids["group"], path))
        text = path.read_text(encoding="utf-8")
        for private in ("DO-NOT-SHARE", "private-profile", "Private customer session", "Example task", note.id):
            assert private not in text
        result = asyncio.run(poc.save_definition(services, ids["group"], path, include_state=["Brief"]))
        assert result["revision"] == 2
        assert poc.read_template(path).name == "General assistant"
        assert "Example task" in path.read_text(encoding="utf-8")
        unchanged = path.read_bytes()
        asyncio.run(poc.save_definition(services, ids["group"], path, include_state=["Brief"]))
        assert path.read_bytes() == unchanged
        assert services.legions.list() == []  # No temporary library templates left behind.
    with poc.open_profile("recipient", initialize=True) as services:
        result = asyncio.run(poc.apply_definition(services, template=path, initialize=True))
        cards = services.world.list_cards()
        group = next(c for c in cards if c.type == "legion")
        conversation = next(c for c in cards if c.type == "conversation")
        assert group.config["workspace_layout"]["root"]["view"]["card_id"] == conversation.id
        assert len(result["node_ids"]) == 4
        with services.database.locked() as db:
            assert not db.execute("SELECT 1 FROM conversation_sessions WHERE title='Private customer session'").fetchone()


def test_live_profile_and_non_development_targets_are_rejected(tmp_path):
    root = development.prepare_profile("poc")
    with file_lock(root / ".oaw-storage.lock"), pytest.raises(ConflictError, match="in use"):
        with poc.open_profile("poc"):
            pytest.fail("Acquired an already open profile")
    (root / "deployment.json").write_text("{}")
    with pytest.raises(ValueError, match="not a deployment"):
        with poc.open_profile("poc"):
            pass
    with pytest.raises(ValueError, match="Profile names"):
        with poc.open_profile("../personal", initialize=True):
            pass
    unmarked = development.PROFILES / "unmarked"
    unmarked.mkdir()
    (unmarked / "private.txt").write_text("keep")
    with pytest.raises(ValueError, match="Refusing to adopt"):
        with poc.open_profile("unmarked", initialize=True):
            pass
    assert (unmarked / "private.txt").read_text() == "keep"


def test_offline_command_rejects_unfinished_run_without_recovering_or_cancelling_it(capsys):
    with poc.open_profile("poc", initialize=True) as services:
        ids = init(services)
        run = services.run_manager.store.create(agent_id=ids["agent"], runtime_provider_id="core.mock", caller_kind="test")
    assert poc.main(["apply", "--preset", "assistant"]) == 1
    assert "Finish or stop active runs" in capsys.readouterr().err
    connection = sqlite3.connect(development.profile_path("poc") / "database/world.sqlite3")
    try:
        assert connection.execute("SELECT status FROM runs WHERE run_id=?", (run.run_id,)).fetchone()[0] == "created"
        assert connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == len(ids)
    finally:
        connection.close()


def test_incompatible_or_oversized_template_does_not_create_cards(tmp_path, monkeypatch):
    path = tmp_path / "legion.json"
    with poc.open_profile("author", initialize=True) as services:
        ids = init(services)
        asyncio.run(poc.save_definition(services, ids["group"], path))
    definition = json.loads(path.read_text())
    definition["blueprint"]["nodes"][1]["type"] = "not.installed.agent"
    path.write_text(json.dumps(definition))
    with poc.open_profile("recipient", initialize=True) as services:
        with pytest.raises(PluginCompatibilityError, match="incompatible"):
            asyncio.run(poc.apply_definition(services, template=path))
        assert services.world.list_cards() == []
    monkeypatch.setattr(poc, "MAX_TEMPLATE_BYTES", 16)
    with pytest.raises(ValueError, match="64 MiB"):
        poc.read_template(path)


def test_failed_export_preserves_file_and_requires_whole_legion(tmp_path, monkeypatch):
    path = tmp_path / "legion.json"
    with poc.open_profile("poc", initialize=True) as services:
        ids = init(services)
        asyncio.run(poc.save_definition(services, ids["group"], path))
        before = path.read_bytes()
        services.world.update_card(ids["agent"], CardPatch(name="Change"))
        original_replace = Path.replace

        def fail_replace(source, destination):
            if destination == path:
                raise OSError("disk write failed")
            return original_replace(source, destination)

        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "replace", fail_replace)
            with pytest.raises(OSError, match="disk write failed"):
                asyncio.run(poc.save_definition(services, ids["group"], path))
        assert path.read_bytes() == before
        assert not list(tmp_path.glob("legion.json.*.tmp"))
        outside = asyncio.run(services.create_card(CardCreate(type="text", content="external")))
        services.world.create_edge(EdgeCreate(source=ids["agent"], target=outside.id, relationship="read"))
        with pytest.raises(ValueError, match="external connected"):
            asyncio.run(poc.save_definition(services, ids["group"], path))
        assert path.read_bytes() == before


def test_command_entry_and_start_reuse_existing_launcher(monkeypatch, capsys):
    assert poc.main(["init", "--preset", "assistant"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "created"
    assert poc.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out)["legions"][0]["name"] == "General assistant"
    calls = []
    monkeypatch.setattr(poc.subprocess, "call", lambda command, **kw: calls.append((command, kw)) or 0)
    assert poc.main(["start", "--frontend-port", "5180"]) == 0
    command, options = calls[0]
    assert command[1:] == [str(poc.ROOT / "scripts/dev.py"), "--profile", "poc", "--backend-port", "8000", "--frontend-port", "5180"]
    assert options["cwd"] == poc.ROOT


def test_knowledge_preset_uses_installed_card_contract_and_closes_clients(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(poc.ROOT / "plugins/knowledge_base/src"))
    from oaw_knowledge_base.plugin import create_plugin
    from oaw_knowledge_base.client import _clients
    registry = create_builtin_registry()
    registry.install(create_plugin())
    monkeypatch.setattr(poc, "create_services", lambda settings, **kwargs: create_services(settings, plugins=registry))
    path = tmp_path / "knowledge.json"
    assert poc.main(["init", "--preset", "knowledge.base.research"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["node_ids"]) == {"group", "agent", "conversation", "knowledge", "sandbox"}
    assert result["node_ids"]["knowledge"] not in _clients
    assert poc.main(["save", str(path), "--legion", "Lab Manager"]) == 0
    capsys.readouterr()
    assert poc.main(["check", "--template", str(path)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["cards"] == 5 and checked["panels"] > 0


def test_connected_workspaces_save_check_preview_and_keep_lineage(tmp_path):
    from backend.hosting.scenarios import template_from_definition
    from backend.deploy import validate_manifest
    path = tmp_path / "workbench.json"
    with poc.open_profile("author", initialize=True) as services:
        first = init(services)
        second = asyncio.run(poc.apply_definition(services, preset="coding"))["node_ids"]
        services.world.update_card(second["sandbox"], CardPatch(config={"network_enabled": True}))
        services.world.create_edge(EdgeCreate(source=first["agent"], target=second["agent"], relationship="communicate"))
        with pytest.raises(ValueError, match="external connected"):
            asyncio.run(poc.save_definition(services, first["group"], path))
        asyncio.run(poc.save_definition(services, None, path))
    before = poc.read_template(path)
    assert next(n for n in before.blueprint.nodes if n.type == "sandbox").config["network_enabled"] is True
    template = template_from_definition(before)
    assert len(template["root_keys"]) == 2
    assert asyncio.run(poc.check_definition(path, "author"))["workspaces"] == 2
    with poc.open_profile("colleague", initialize=True) as services:
        asyncio.run(poc.apply_definition(services, template=path))
        unchanged = path.read_bytes()
        asyncio.run(poc.save_definition(services, None, path))
        assert path.read_bytes() == unchanged
        groups = poc.select_groups(services)
        assert len(groups) == 2
        agent = next(c for c in services.world.list_cards() if c.type == "agent")
        services.world.update_card(agent.id, CardPatch(name="Reviewed agent"))
        asyncio.run(poc.save_definition(services, [g.id for g in groups], path))
        stable = path.read_bytes()
        asyncio.run(poc.save_definition(services, None, path))
        assert path.read_bytes() == stable
    after = template_from_definition(poc.read_template(path))
    assert after["source_nodes"] == template["source_nodes"]
    assert after["root_keys"] == template["root_keys"]
    assert len(after["blueprint"]["edges"]) == len(before.blueprint.edges)
    result = asyncio.run(poc.prepare_preview("colleague", agent_runtime="core.mock"))
    manifest = json.loads((Path(result["path"]) / "deployment.json").read_text())
    validate_manifest(manifest)
    assert len(manifest["workspaces"]) == 2


def test_committed_poc_has_three_connected_workspaces_and_no_cloud_state():
    from backend.hosting.scenarios import template_from_definition
    definition = poc.read_template(poc.ROOT / "examples/poc/legion.json")
    assert definition.name == "实验工作台"
    template = template_from_definition(definition)
    nodes = {n.key: n for n in definition.blueprint.nodes}
    assert [nodes[k].name for k in template["root_keys"]] == ["Lab Manager", "XRD 分析工作台", "MatCreator research"]
    assert len(nodes) == 65
    assert any(n.type == "theafish.deepseek-harness.agent" and n.config["permission_mode"] == "host-default" for n in nodes.values())
    assert any(nodes[e.source].parent_key != nodes[e.target].parent_key for e in definition.blueprint.edges)
    for node in nodes.values():
        assert node.initial_shared_state is None
        # The only initial content is MatCreator's bundled public skill library.
        if node.type not in {"matcreator.kdg", "matcreator.kdg.skill"}:
            assert node.initial_document is None
        assert not node.config.get("workspace_path")
        assert not node.config.get("dsh_home")
        assert not node.config.get("dsh_command")


def test_explicit_team_pack_installation_uses_isolated_profile_and_lock(tmp_path):
    from backend.packs.installation import PackInstallationManager
    from backend.tests.test_pack_installation import artifact
    path = tmp_path / "team.oawpack"
    path.write_bytes(artifact())
    root = development.prepare_profile("team")
    with file_lock(root / ".oaw-storage.lock"), pytest.raises(ConflictError, match="in use"):
        asyncio.run(poc.install_pack("team", path))
    asyncio.run(poc.install_pack("team", path))
    assert PackInstallationManager(root).selected()["test.greeter"].version == "0.1.0"
    assert asyncio.run(poc.install_pack("team", path))["status"] == "reused"
    (root / "deployment.json").write_text("{}")
    with pytest.raises(ValueError, match="editable development"):
        asyncio.run(poc.install_pack("team", path))


def test_including_container_content_preserves_owned_documents(tmp_path):
    path = tmp_path / "skills.json"
    with poc.open_profile("author", initialize=True) as services:
        ids = init(services)
        child = asyncio.run(services.create_card(CardCreate(type="text", name="Shared brief",
            parent_id=ids["group"], content="Reusable example instructions")))
        asyncio.run(poc.save_definition(services, ids["group"], path,
                                       include_state=[services.world.get_card(ids["group"]).name]))
    definition = poc.read_template(path)
    note = next(n for n in definition.blueprint.nodes if n.name == child.name)
    assert note.payload == {"content": "Reusable example instructions"}


def test_colleagues_keep_stable_template_keys_and_hosted_lineage(tmp_path):
    from backend.hosting.scenarios import template_from_definition
    path = tmp_path / "legion.json"
    with poc.open_profile("author", initialize=True) as services:
        ids = init(services)
        asyncio.run(poc.save_definition(services, ids["group"], path))
    first = poc.read_template(path)
    with poc.open_profile("colleague", initialize=True) as services:
        ids = asyncio.run(poc.apply_definition(services, template=path))["node_ids"]
        services.world.update_card(ids["agent"], CardPatch(name="Improved agent"))
        asyncio.run(poc.save_definition(services, ids["group"], path))
    second = poc.read_template(path)
    before, after = template_from_definition(first), template_from_definition(second)
    assert before["source_nodes"] == after["source_nodes"]
    assert before["source_identity"] == after["source_identity"]
    assert before["source_release_id"] != after["source_release_id"]
    assert second.revision == first.revision + 1


def test_local_preview_is_real_published_snapshot_with_independent_writes(tmp_path):
    from backend.deploy import validate_manifest
    with poc.open_profile("poc", initialize=True) as services:
        ids = init(services)
        note = asyncio.run(services.create_card(CardCreate(type="text", parent_id=ids["group"], content="Source document")))
    result = asyncio.run(poc.prepare_preview("poc", ids["group"]))
    root = Path(result["path"])
    manifest = json.loads((root / "deployment.json").read_text())
    validate_manifest(manifest)
    assert manifest["id"] == result["release_id"]
    assert manifest["secure_cookie"] is False
    assert result["password"] not in (root / "deployment.json").read_text()
    assert root.parent.name == "poc" and root.parent.parent.name == "previews"
    with sqlite3.connect(root / "database/world.sqlite3") as db:
        db.execute("UPDATE cards SET name='Only the preview changed' WHERE id=?", (note.id,))
    with poc.open_profile("poc") as services:
        assert services.world.get_card(note.id).name != "Only the preview changed"
        assert not (services.settings.data_root / "deployment.json").exists()


def test_check_rejects_unpublishable_git_scene_without_editing_author(tmp_path):
    path = tmp_path / "legion.json"
    with poc.open_profile("poc", initialize=True) as services:
        ids = init(services)
        asyncio.run(poc.save_definition(services, ids["group"], path))
        count = len(services.world.list_cards())
    assert asyncio.run(poc.check_definition(path))["status"] == "ready"
    value = json.loads(path.read_text())
    group = next(node for node in value["blueprint"]["nodes"] if node["type"] == "legion")
    group["config"].pop("workspace_layout", None)
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, DomainError), match="[Ll]ayout|[Ww]orkspace"):
        asyncio.run(poc.check_definition(path))
    with poc.open_profile("poc") as services:
        assert len(services.world.list_cards()) == count
