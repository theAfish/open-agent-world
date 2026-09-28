import base64
import pymupdf

from backend.tests.conftest import create_node


def pdf(text):
    document = pymupdf.open()
    document.new_page().insert_text((50, 50), text)
    encoded = base64.b64encode(document.tobytes()).decode()
    document.close()
    return encoded


def read(client, identifier):
    return client.get(f"/api/nodes/{identifier}/document").json()


def attach(client, identifier, content, kind="main", revision=None):
    return client.post(f"/api/literature/papers/{identifier}/attach_pdf", json={
        "expected_revision": read(client, identifier)["revision"] if revision is None else revision,
        "arguments": {"filename": "fixture.pdf", "pdf": content, "kind": kind}})


def scope(client):
    node = create_node(client, "literature.scope")
    response = client.post(f"/api/nodes/{node['id']}/actions/revise", json={"expected_revision": read(client, node["id"])["revision"],
        "arguments": {"question": "Synthetic source-bound intake", "budget": {"max_searches": 3, "max_papers": 10}}})
    assert response.status_code == 200, response.text
    return node["id"]


def collect(client, scope_id, paper_id, **extra):
    document = read(client, paper_id)
    return client.post(f"/api/literature/scopes/{scope_id}/collect_reference", json={
        "expected_revision": read(client, scope_id)["revision"], "arguments": {
            "source_paper_id": paper_id, "source_document_version_id": document["value"]["current_document_version_id"],
            "reference_page": 1, "reference_text": "Example 2024. DOI 10.1234/intake-test", "doi": "10.1234/intake-test", **extra}})


def test_supplement_is_independent_and_retry_preserves_main_annotations(client):
    node = create_node(client, "library.paper")
    assert attach(client, node["id"], pdf("Main paper")).status_code == 200
    current = read(client, node["id"])
    response = client.post(f"/api/nodes/{node['id']}/actions/annotate", json={"expected_revision": current["revision"],
        "arguments": {"notes": "Keep my main reading notes"}})
    assert response.status_code == 200, response.text
    before = read(client, node["id"])["value"]
    content = pdf("Supplement methods and tables")
    result = attach(client, node["id"], content, "supplement")
    assert result.status_code == 200, result.text
    after = read(client, node["id"])["value"]
    assert after["pdf"] == before["pdf"] and after["notes"] == before["notes"]
    assert after["current_document_version_id"] == before["current_document_version_id"]
    assert len(after["attachments"]) == 1
    child = read(client, after["attachments"][0]["paper_id"])["value"]
    assert child["pdf"] == content and child["versions"][-1]["kind"] == "supplement"
    assert attach(client, node["id"], content, "supplement").json()["replay"]
    assert len(read(client, node["id"])["value"]["attachments"]) == 1


def test_invalid_attachment_and_stale_revision_do_not_create_nodes(client):
    node = create_node(client, "library.paper")
    before = client.get("/api/nodes").json()
    assert attach(client, node["id"], "not-pdf", "supplement").status_code == 422
    assert attach(client, node["id"], pdf("SI"), "supplement", revision=99).status_code == 409
    assert client.get("/api/nodes").json() == before


def test_citation_collect_is_local_deduplicated_and_retains_source_version(client):
    source = create_node(client, "library.paper")
    assert attach(client, source["id"], pdf("References\nExample 2024. DOI 10.1234/intake-test")).status_code == 200
    owner = scope(client)
    result = collect(client, owner, source["id"])
    assert result.status_code == 200, result.text
    target = result.json()["paper_id"]
    assert result.json()["created"]
    assert read(client, target)["value"]["metadata"]["doi"] == "10.1234/intake-test"
    assert not read(client, target)["value"]["pdf"]
    again = collect(client, owner, source["id"])
    assert again.status_code == 200 and again.json()["replay"]
    value = read(client, owner)["value"]
    assert set(value["paper_ids"]) == {source["id"], target}
    citations = [item["citation"] for item in value["exploration_links"] if "citation" in item]
    assert len(citations) == 1 and citations[0]["verification"] == "unreviewed"
    assert citations[0]["source_document_version_id"] == read(client, source["id"])["value"]["current_document_version_id"]
    assert not value["search_runs"] and not value["evidence"]


def test_citation_rejects_unlocated_doi_and_old_version(client):
    source = create_node(client, "library.paper")
    assert attach(client, source["id"], pdf("No reference DOI on this page")).status_code == 200
    owner = scope(client)
    assert collect(client, owner, source["id"]).status_code == 422
    assert collect(client, owner, source["id"], source_document_version_id="a"*64).status_code == 422
    assert not read(client, owner)["value"]["paper_ids"]


def test_explicit_paper_association_is_idempotent_and_never_title_matching(client):
    first = create_node(client, "library.paper", name="same filename")
    second = create_node(client, "library.paper", name="same filename")
    owner = scope(client)
    for _ in range(2):
        response = client.post(f"/api/literature/scopes/{owner}/link_paper", json={"expected_revision": read(client, owner)["revision"], "arguments": {"paper_id": second["id"]}})
        assert response.status_code == 200, response.text
    assert read(client, owner)["value"]["paper_ids"] == [second["id"]]
    options = client.get("/api/literature/papers/intake-options").json()
    assert {paper["id"] for paper in options["papers"]} >= {first["id"], second["id"]}


def citation_roads(client):
    from backend.tests.test_literature_exploration import frontier, action
    source = create_node(client, "library.paper")
    assert attach(client, source["id"], pdf("References\nExample 2024. DOI 10.1234/intake-test")).status_code == 200
    owner = scope(client)
    assert client.post(f"/api/literature/scopes/{owner}/link_paper", json={"expected_revision": read(client, owner)["revision"],
        "arguments": {"paper_id": source["id"]}}).status_code == 200
    source_road = "route:" + frontier(client, {"id": owner}, "Source reading path")
    destination = "route:" + frontier(client, {"id": owner}, "Selected collection path")
    assert action(client, {"id": owner}, "move_member", road_id=source_road, entity_id="paper:" + source["id"]).status_code == 200
    return owner, source["id"], source_road, destination


def test_citation_new_paper_joins_explicit_road_once_and_keeps_citation_provenance(client):
    owner, source_id, source_road, destination = citation_roads(client)
    result = collect(client, owner, source_id, road_id=destination)
    assert result.status_code == 200, result.text
    target = result.json()["paper_id"]
    value = read(client, owner)["value"]
    roads = {road["id"]: road for road in value["exploration_roads"]}
    assert "paper:" + target in roads[destination]["member_ids"]
    assert "paper:" + source_id in roads[source_road]["member_ids"]
    assert sum(road["member_ids"].count("paper:" + target) for road in roads.values()) == 1
    assert len([entity for entity in value["exploration_nodes"] if entity["id"] == "paper:" + target]) == 1
    citation = next(link for link in value["exploration_links"] if link.get("citation"))
    assert citation["relation"] == "related"
    assert citation["citation"]["road_id"] == destination and citation["citation"]["text_match"] == "normalized_page_text"
    assert citation["citation"]["verification"] == "unreviewed"
    edges = client.get("/api/edges").json()
    assert any(edge["target"] == target and edge["relationship"] == "literature.road" for edge in edges)
    again = collect(client, owner, source_id, road_id=destination)
    assert again.status_code == 200 and again.json()["replay"]
    assert len([link for link in read(client, owner)["value"]["exploration_links"] if link.get("citation")]) == 1


def test_citation_existing_paper_stays_on_its_road_with_cross_path_link(client):
    from backend.tests.test_literature_exploration import action
    owner, source_id, source_road, destination = citation_roads(client)
    target = create_node(client, "library.paper", name="Retained cited work", config={"doi": "10.1234/intake-test"})
    assert client.post(f"/api/literature/scopes/{owner}/link_paper", json={"expected_revision": read(client, owner)["revision"],
        "arguments": {"paper_id": target["id"]}}).status_code == 200
    assert action(client, {"id": owner}, "move_member", road_id=destination, entity_id="paper:" + target["id"]).status_code == 200
    before = client.get(f"/api/nodes/{target['id']}").json()["position"]
    result = collect(client, owner, source_id, road_id=source_road)
    assert result.status_code == 200, result.text
    assert result.json()["paper_id"] == target["id"] and not result.json()["created"]
    assert result.json()["citation"]["navigation"] == "retained_existing_road"
    value = read(client, owner)["value"]
    membership = [road["id"] for road in value["exploration_roads"] if "paper:" + target["id"] in road["member_ids"]]
    assert membership == [destination]
    assert client.get(f"/api/nodes/{target['id']}").json()["position"] == before
    assert any(link.get("citation") and link["source"] == "paper:" + source_id and link["target"] == "paper:" + target["id"] for link in value["exploration_links"])


def test_citation_defaults_to_source_road_and_rejects_foreign_road_before_mutating(client):
    owner, source_id, source_road, _ = citation_roads(client)
    before = client.get("/api/nodes").json()
    assert collect(client, owner, source_id, road_id="route:foreign").status_code == 422
    assert client.get("/api/nodes").json() == before
    result = collect(client, owner, source_id)
    assert result.status_code == 200, result.text
    assert result.json()["citation"]["road_id"] == source_road


def test_citation_rejects_fabricated_text_even_when_the_doi_is_on_page(client):
    from backend.node_documents import read_document, write_document
    source = create_node(client, "library.paper")
    assert attach(client, source["id"], pdf("References\nExample 2024. DOI 10.1234/intake-test")).status_code == 200
    owner = scope(client)
    fabricated = "Invented authors and fabricated result. DOI 10.1234/intake-test"
    # Cached editable page text is not authority for source provenance.
    current = read_document(client.app.state.services, source["id"])
    write_document(client.app.state.services, source["id"], {**current["value"], "text": [fabricated]}, current["revision"])
    result = collect(client, owner, source["id"], reference_text=fabricated)
    assert result.status_code == 422
    assert not read(client, owner)["value"]["paper_ids"]
    assert not read(client, owner)["value"]["exploration_links"]


def test_citation_matches_wrapped_original_with_punctuation(client):
    from backend.library_intake import normalized_reference
    source = create_node(client, "library.paper")
    original = "Example, A. (2024). An ion-transport study.\nDOI: 10.1234/intake-test."
    assert attach(client, source["id"], pdf("References\n" + original)).status_code == 200
    owner = scope(client)
    result = collect(client, owner, source["id"], reference_text=original.replace("\n", "  "))
    assert result.status_code == 200, result.text
    assert normalized_reference("‘Ion–transport’\n (2024)") == normalized_reference("'Ion-transport' (2024)")
