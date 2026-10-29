"""Project boundaries, durable import retries, and untrusted archive handling."""
import base64
import io
import tarfile
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from oaw_knowledge_base import actions, client, projects
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings
from oaw_knowledge_base.errors import KnowledgeError


@pytest.fixture
def card(tmp_path):
    identifier = str(uuid.uuid4())
    context = KnowledgeContext(identifier, tmp_path / identifier, state=MemorySettings())
    yield context
    client.close_client(identifier)


def upload(path, data=b"# Sample\nMeasured value 42."):
    return {"filename": path.split("/")[-1], "relative_path": path,
            "content_base64": base64.b64encode(data).decode()}


def preview(card, files=None, **kwargs):
    return projects.project_import_preview(card, {"files": files or [upload("a.md")], **kwargs})


def commit(card, item, **kwargs):
    return projects.project_import_commit(card, {
        "token": item["token"], "grouping": item["grouping"], "depth": item["depth"],
        "auto_process": False, **kwargs,
    })


def test_folder_grouping_is_preview_only_and_reuses_upload(card):
    item = preview(card, [upload("batch/A/paper.md"), upload("batch/A/SI/table.csv"), upload("batch/B/report.md")],
                   grouping="depth", depth=2)
    assert [(project["key"], project["file_count"]) for project in item["projects"]] == [("batch/A", 2), ("batch/B", 1)]
    assert projects.projects(card, {})["projects"] == []
    alternate = projects.project_import_preview(card, {"token": item["token"], "grouping": "single"})
    assert alternate["token"] == item["token"]
    assert len(alternate["projects"]) == 1
    imported = commit(card, item)
    assert len(imported["projects"]) == 2
    project = next(project for project in imported["projects"] if project["name"] == "A")
    detail = projects.projects(card, {"project_id": project["id"]})["project"]
    assert {file["relative_path"] for file in detail["files"]} == {"paper.md", "SI/table.csv"}
    assert detail["state"] == "pending"


@pytest.mark.parametrize(("grouping", "depth", "expected"), [
    ("top", 1, 1), ("leaf", 1, 2), ("single", 1, 1), ("depth", 2, 2),
    ("depth", 0, 3), ("files", 1, 3),
])
def test_each_supported_grouping_has_explicit_project_boundaries(card, grouping, depth, expected):
    item = preview(card, [upload("root/A/a.md"), upload("root/A/b.md"), upload("root/B/c.md")],
                   grouping=grouping, depth=depth)
    assert len(item["projects"]) == expected


def test_commit_survives_restart_and_prevents_different_plan(card):
    item = preview(card, [upload("A/paper.md"), upload("A/SI.md")])
    initial = commit(card, item)
    client.close_client(card.node_id)
    repeated = commit(card, item)
    assert repeated["reused"] is True
    assert repeated["source_ids"] == initial["source_ids"]
    assert len(projects.projects(card, {})["projects"]) == 1
    with pytest.raises(KnowledgeError, match="different committed"):
        commit(card, item, grouping="files")


def test_attachment_archive_identity_reopens_its_committed_projects(card):
    files = [{"filename": "bundle.zip", "data": zip_bytes([("A/main.md", b"report")])}]
    item = projects.preview_files(card, files, identity="artifact:version:path")
    assert projects.preview_files(card, files, identity="artifact:version:path")["token"] == item["token"]
    imported = commit(card, item)
    client.close_client(card.node_id)
    repeated = projects.preview_files(card, files, identity="artifact:version:path")
    assert repeated["committed"] is True
    assert repeated["result"]["source_ids"] == imported["source_ids"]
    assert repeated["result"]["reused"] is True
    with pytest.raises(KnowledgeError, match="different content"):
        projects.preview_files(card, [{"filename": "bundle.zip", "data": zip_bytes([("A/main.md", b"changed")])}],
                               identity="artifact:version:path")


def test_concurrent_retries_create_one_project_and_file(card):
    item = preview(card)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(card, item), range(2)))
    assert results[0]["source_ids"] == results[1]["source_ids"]
    assert sorted(result["reused"] for result in results) == [False, True]
    assert len(projects.projects(card, {})["projects"]) == 1


def test_add_si_reuses_existing_project_and_replaces_only_current_file_revision(card):
    first = preview(card, [upload("paper.md", b"v1")], grouping="files")
    imported = commit(card, first)
    project_id = imported["projects"][0]["id"]
    second = preview(card, [upload("paper.md", b"v2"), upload("SI.csv", b"sample,value\nA,42")], grouping="single")
    result = commit(card, second, projects=[{"key": "__all__", "name": "Existing", "project_id": project_id}])
    detail = projects.projects(card, {"project_id": project_id})["project"]
    assert len(projects.projects(card, {})["projects"]) == 1
    assert detail["source_count"] == 2
    assert imported["source_ids"][0] not in {file["id"] for file in detail["files"]}
    kb = client.open_client(card.node_id, card.storage_path)
    assert kb.sources.read_bytes(imported["source_ids"][0]) == b"v1"
    again = preview(card, [upload("paper.md", b"v2")], grouping="single")
    duplicate = commit(card, again, projects=[{"key": "__all__", "name": "Existing", "project_id": project_id}])
    assert duplicate["source_ids"][0] in result["source_ids"]
    assert len(kb.sources.list(collection_id=project_id)) == 3


def test_project_conversion_reuses_completed_artifacts_and_does_not_process_other_projects(card):
    selected = commit(card, preview(card, [upload("A.md")], grouping="files"))["projects"][0]
    other = commit(card, preview(card, [upload("B.md")], grouping="files"))["projects"][0]
    processing = projects.project_process(card, {"project_ids": [selected["id"]]})
    kb = client.open_client(card.node_id, card.storage_path)
    assert kb.jobs.wait(processing["jobs"][0]["job"]["id"], timeout=30).status == "COMPLETED"
    assert projects.projects(card, {"project_id": selected["id"]})["project"]["state"] == "ready"
    assert projects.projects(card, {"project_id": other["id"]})["project"]["state"] == "pending"
    assert projects.project_process(card, {"project_ids": [selected["id"]]})["jobs"][0]["skipped"] == "already converted"


def test_replaced_file_is_absent_from_current_sources_and_search_but_keeps_original_evidence(card):
    old = commit(card, preview(card, [upload("report.md", b"# Report\nLegacyquartz result.")], grouping="single"), auto_process=True)
    project_id = old["projects"][0]["id"]
    kb = client.open_client(card.node_id, card.storage_path)
    assert kb.jobs.wait(old["jobs"][0]["job"]["id"], timeout=30).status == "COMPLETED"
    replacement = commit(card, preview(card, [upload("report.md", b"# Report\nCurrentgranite result.")], grouping="single"),
        projects=[{"key": "__all__", "name": "Existing", "project_id": project_id}], auto_process=True)
    assert kb.jobs.wait(replacement["jobs"][0]["job"]["id"], timeout=30).status == "COMPLETED"
    assert [item["id"] for item in actions.sources(card, {"group_id": project_id})["sources"]] == replacement["source_ids"]
    assert actions.search(card, {"query": "Legacyquartz"})["results"] == []
    found = actions.search(card, {"query": "Currentgranite", "group_id": project_id})["results"]
    assert [item["source_id"] for item in found] == replacement["source_ids"]
    assert "Legacyquartz" in actions.markdown(card, {"source_id": old["source_ids"][0]})["markdown"]


def test_preview_tokens_are_private_to_actor_and_card(card, tmp_path):
    item = preview(card)
    other_actor = replace(card, actor_id="agent")
    with pytest.raises(KnowledgeError, match="not found"):
        commit(other_actor, item)
    other = KnowledgeContext(str(uuid.uuid4()), tmp_path / "other", state=MemorySettings())
    try:
        with pytest.raises(KnowledgeError, match="not found"):
            commit(other, item)
    finally:
        client.close_client(other.node_id)


def test_invalid_assignment_and_unknown_project_fail_before_writes(card):
    item = preview(card)
    with pytest.raises(KnowledgeError, match="outside this preview"):
        commit(card, item, assignments=[{"file_id": "forged", "project_key": "__root__"}])
    with pytest.raises(Exception, match="not found|does not exist|Unknown"):
        commit(card, item, projects=[{"key": "__root__", "name": "Missing", "project_id": str(uuid.uuid4())}])
    assert projects.projects(card, {})["projects"] == []
    assert len(commit(card, item)["projects"]) == 1


def zip_bytes(entries):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return content.getvalue()


def test_archive_project_paths_and_excluded_files(card):
    data = zip_bytes([("reports/A/main.md", b"main"), ("reports/A/SI.csv", b"si"), ("reports/B/report.md", b"report")])
    item = preview(card, [upload("reports.zip", data)], grouping="depth", depth=2)
    excluded = next(file for file in item["files"] if file["filename"] == "report.md")
    imported = commit(card, item, assignments=[{"file_id": excluded["id"], "project_key": None}])
    assert len(imported["projects"]) == 1
    assert imported["projects"][0]["source_count"] == 2


@pytest.mark.parametrize("path", ["../escape.md", "/absolute.md", "C:/host.md", "folder/../../escape", "x\\..\\escape"])
def test_archive_traversal_is_rejected_before_any_staging_or_project_write(card, path):
    with pytest.raises(KnowledgeError, match="safe relative"):
        preview(card, [upload("unsafe.zip", zip_bytes([(path, b"bad")]))])
    assert projects.projects(card, {})["projects"] == []


def test_archive_links_are_rejected(card):
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        info = tarfile.TarInfo("linked")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(KnowledgeError, match="links"):
        preview(card, [upload("unsafe.tar", content.getvalue())])


def test_standard_tar_relative_root_preserves_project_files(card):
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        data = b"# Report"
        info = tarfile.TarInfo("./sample/report.md")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    item = preview(card, [upload("reports.tar", content.getvalue())])
    assert item["files"][0]["relative_path"] == "sample/report.md"
    assert item["projects"][0]["name"] == "sample"


def test_archive_expansion_limits_are_checked_before_materialization(card):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.txt", b"a" * 100000)
    with pytest.raises(KnowledgeError, match="expansion limit"):
        preview(card, [upload("bomb.zip", content.getvalue())])


def test_preview_rejects_duplicate_paths_and_bad_base64(card):
    with pytest.raises(KnowledgeError, match="duplicate path"):
        preview(card, [upload("same.md"), upload("same.md")])
    with pytest.raises(KnowledgeError, match="valid base64"):
        preview(card, [{"filename": "bad.pdf", "content_base64": "!not64"}])


def test_project_inventory_rejects_sources_from_other_projects(card):
    imported = commit(card, preview(card, [upload("A.md"), upload("B.md")], grouping="files"))
    kb = client.open_client(card.node_id, card.storage_path)
    first, second = imported["projects"]
    source = projects.project_sources(kb, first["id"])[0]
    with pytest.raises(KnowledgeError, match="does not belong"):
        projects.register_project_file(kb, second["id"], source)
