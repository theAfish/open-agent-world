from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.tests.conftest import create_node
import asyncio
from backend.capabilities.provider import WorldAgentCapabilityProvider


def test_collection_identity_layout_edges_and_reload(tmp_path):
    settings=Settings.for_data_root(tmp_path / "collection")
    with TestClient(create_app(settings)) as client:
        collection=create_node(client,"core.shadow-collection",position={"x":300,"y":300})
        cid=collection["id"]
        assert collection["config"]["display_state"]=="minimal"
        members=[create_node(client,t,parent_id=cid,position={"x":100+i*250,"y":500}) for i,t in enumerate(["text","image","agent","conversation"])]
        outside=create_node(client,"agent")
        edges=[]
        for source,target,relationship in [(outside["id"],cid,"core.collection.inspect"),(members[2]["id"],members[0]["id"],"read"),(members[2]["id"],members[3]["id"],"participate")]:
            response=client.post("/api/edges",json={"source":source,"target":target,"relationship":relationship})
            assert response.status_code==201,response.text
            edges.append(response.json())
        for state in ["stacked","expanded","stacked","minimal","expanded"]:
            result=client.patch(f"/api/nodes/{cid}",json={"config":{"display_state":state}})
            assert result.status_code==200,result.text
        invalid=client.patch(f"/api/nodes/{cid}",json={"config":{"display_state":"broken"}})
        assert invalid.status_code>=400
        # One membership reference; repeated assignment cannot duplicate a member.
        for _ in range(2):
            assert client.patch(f"/api/nodes/{members[0]['id']}",json={"parent_id":cid}).status_code==200
        assert client.patch(f"/api/nodes/{cid}",json={"position":{"x":500,"y":400}}).status_code==200
        original_ids={m["id"] for m in members}
    with TestClient(create_app(settings)) as client:
        world=client.get("/api/world").json()
        kept=[n for n in world["nodes"] if n.get("parent_id")==cid]
        assert {n["id"] for n in kept}==original_ids
        assert len(kept)==4
        assert client.get(f"/api/nodes/{members[0]['id']}").json()["position"]=={"x":300,"y":600}
        assert client.get(f"/api/nodes/{cid}").json()["config"]["display_state"]=="expanded"
        assert {(e["id"],e["source"],e["target"]) for e in world["edges"]}=={(e["id"],e["source"],e["target"]) for e in edges}


def test_collection_inspection_does_not_grant_member_contents(client):
    collection=create_node(client,"core.shadow-collection")
    agent=create_node(client,"agent")
    text=create_node(client,"text",parent_id=collection["id"],content="Private member contents")
    edge=client.post("/api/edges",json={"source":agent["id"],"target":collection["id"],"relationship":"core.collection.inspect"})
    assert edge.status_code==201
    provider=WorldAgentCapabilityProvider(client.app.state.services)
    async def inspect():
        tools=await provider.list_tools(agent["id"])
        tool=next(tool for tool in tools if tool.name=="list_collection_members")
        selector=tool.input_schema["properties"]["collection"]["enum"][0]
        return await provider.invoke_tool(agent["id"],tool.capability_id,{"collection":selector})
    result=asyncio.run(inspect())
    assert result["members"]==[{"id":text["id"],"name":text["name"],"type":"text"}]
    assert "Private member contents" not in str(result)
    assert client.get(f"/api/agents/{agent['id']}/resources/{text['id']}/text").status_code==403
