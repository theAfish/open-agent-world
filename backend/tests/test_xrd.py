import base64
import hashlib
import json
from pathlib import Path
import subprocess
import os
import asyncio
from types import SimpleNamespace

import pytest

from backend.tests.conftest import create_node
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError


REFERENCE = b'PDF#35-0754: QM=Common(+)\nLithium Titanium Phosphate\nLiTi2(PO4)3\nRadiation=CuKa1 Lambda=1.5406\n2-Theta d I (h k l)\n20.843 4.2582 50.0 (1 0 4)\n24.491 3.6316 100.0 (1 1 3)\n'


def upload(client, node, raw, revision=None):
    if revision is None:
        revision = client.get(f"/api/nodes/{node['id']}/document").json()["revision"]
    return client.post(f"/api/nodes/{node['id']}/actions/import", json={"expected_revision": revision,
        "arguments": {"filename": "input.txt", "source_base64": base64.b64encode(raw).decode()}})


def test_xrd_inputs_parse_preserve_source_and_reject_binary(client):
    node = create_node(client, "xrd.spectrum-canvas")
    raw = b'*FILE_TYPE "RAS_RAW"\n*HW_XG_WAVE_LENGTH_ALPHA1 "1.540593"\n' + '\n'.join(f'{10+i*.02:.4f} {100+i}' for i in range(30)).encode()
    result = upload(client, node, raw)
    assert result.status_code == 200, result.text
    doc = result.json()["value"]
    assert doc["kind"] == "pattern" and len(doc["points"]) == 30
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
    assert base64.b64decode(doc["source_base64"]) == raw
    assert doc["metadata"]["HW_XG_WAVE_LENGTH_ALPHA1"] == "1.540593"
    assert upload(client, node, b'\x88\x7d\x00fake', 2).status_code == 422
    assert upload(client, node, raw, 0).status_code == 409
    reference = create_node(client, "xrd.reference")
    parsed = upload(client, reference, REFERENCE)
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["value"]["peaks"][0]["hkl"] == [1, 0, 4]
    assert parsed.json()["value"]["metadata"]["reference_code"] == "35-0754"
    cif = create_node(client, "xrd.cif")
    assert upload(client, cif, REFERENCE).status_code == 422


def test_xrd_read_access_is_connection_scoped_and_revocable(client):
    reference = create_node(client, "xrd.reference")
    unrelated = create_node(client, "xrd.reference")
    agent = create_node(client, "xrd.match")
    assert upload(client, reference, REFERENCE).status_code == 200
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': reference['id'], 'relationship': 'xrd.input'})
    assert edge.status_code == 201, edge.text
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = client.portal.call(provider.list_tools, agent['id'])
    reader = next(t for t in tools if t.name == 'read_xrd_input')
    result = client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': reference['id']})
    assert result['node_id'] == reference['id'] and result['revision'] == 2
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': unrelated['id']})
    client.delete(f"/api/edges/{edge.json()['id']}")
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': reference['id']})


def test_cif_association_is_explicit_and_remapped(client):
    from oaw_xrd import XRDPlugin
    from oaw_xrd.documents import associate
    assert associate({'kind': 'cif'}, {'reference_node_id': 'ref'})['reference_node_id'] == 'ref'
    with pytest.raises(ResourceValidationError):
        associate({'kind': 'reference'}, {'reference_node_id': 'ref'})
    class Registration:
        nodes = {}
        def register_node_type(self, definition): self.nodes[definition.id] = definition
        def __getattr__(self, name): return lambda *a, **kw: None
    registration = Registration()
    XRDPlugin().register(registration)
    remap = registration.nodes['xrd.cif'].document.remap_references
    assert remap({'reference_node_id': 'ref'}, {'ref': 'copied'})['reference_node_id'] == 'copied'
    assert remap({'reference_node_id': 'ref'}, {})['reference_node_id'] == ''


def test_peak_match_handles_missing_cif_unexplained_peak_and_wavelength(tmp_path):
    # Match worker runs in the same isolated scientific interpreter as PyWPEM.
    root = Path(__file__).resolve().parents[2]
    engine_root = Path(os.environ.get('OAW_XRD_ROOT', str(root.parent / 'XRD')))
    python = Path(os.environ.get('OAW_XRD_PYTHON', str(engine_root / '.venv-xrd' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))))
    if not python.exists():
        pytest.skip('Local XRD scientific interpreter is not installed')
    script = '''
import json, math, sys
sys.path.insert(0, sys.argv[1])
from matching import match_patterns
points = [[10+i*.01, 100+1000*math.exp(-((10+i*.01-20.04)/.07)**2)+500*math.exp(-((10+i*.01-30)/.08)**2)] for i in range(3001)]
inputs=[{'node_id':'p','revision':1,'value':{'kind':'pattern','filename':'synthetic','sha256':'p','metadata':{},'points':points}},
{'node_id':'r','revision':1,'value':{'kind':'reference','filename':'ref','sha256':'r','metadata':{},'peaks':[{'two_theta':20,'d':1.540593/(2*math.sin(math.radians(10))),'intensity':100,'hkl':[1,0,0]}]}}]
options={'wavelength':1.540593,'tolerance_deg':.15,'prominence_fraction':.03,'smoothing_deg':.03,'min_peak_distance':.1,'reference_min_intensity':5}
r=match_patterns(inputs,options)
assert len(r['observed_peaks'])==2 and len(r['unexplained_peaks'])==1
assert abs(r['candidates'][0]['matches'][0]['delta']-.04)<.011
assert r['candidates'][0]['fit_input_status']=='missing_cif'
inputs.append({'node_id':'c','revision':1,'value':{'kind':'cif','reference_node_id':'other','text':'data_c','sha256':'c','filename':'c.cif'}})
assert not match_patterns(inputs,options)['candidates'][0]['cifs']
inputs[-1]['value']['reference_node_id']='r'
assert match_patterns(inputs,options)['candidates'][0]['cifs'][0]['node_id']=='c'
options['wavelength']=1.0
assert not match_patterns(inputs,options)['candidates'][0]['matches']
print('synthetic match passed')
'''
    completed = subprocess.run([str(python), '-c', script, str(root / 'plugins/xrd/src/oaw_xrd')], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_connected_fit_requires_an_explicit_valid_cif_association(client, tmp_path):
    from oaw_xrd.runtime import XRDRuntime
    inputs = [
        {'node_id': 'p', 'value': {'kind': 'pattern', 'points': [[10, 1], [11, 2]]}},
        {'node_id': 'r', 'value': {'kind': 'reference', 'peaks': [{'two_theta': 10}]}},
    ]
    class Provider:
        async def list_tools(self, agent_id):
            return [SimpleNamespace(name='read_xrd_input', capability_id='reader', input_schema={'properties': {'target': {'enum': list(range(len(inputs)))}}})]
        async def invoke_tool(self, agent_id, capability_id, arguments):
            return inputs[arguments['target']]
    runtime = XRDRuntime(Provider())
    runtime.root = tmp_path
    runtime.run_root = tmp_path / 'runs'
    config = SimpleNamespace(agent_id='fit', provider_config={'connected_inputs': True, 'demo': False})
    async def first():
        async for _ in runtime.execute(config, SimpleNamespace(run_id='test'), None):
            pass
    with pytest.raises(ValueError, match='候选 CIF'):
        asyncio.run(first())
    inputs.append({'node_id': 'c', 'value': {'kind': 'cif', 'text': 'data_test', 'reference_node_id': 'unconnected'}})
    with pytest.raises(ValueError, match='关联本次连接的标准卡片'):
        asyncio.run(first())
    assert not runtime.run_root.exists()


def test_reference_library_configuration_validates_schema_and_preserves_revision(client, tmp_path):
    import sqlite3
    path = tmp_path / 'cod.sq'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE id (id INTEGER, chemical_formula TEXT, dvalue TEXT, intensita TEXT)')
        db.execute("INSERT INTO id VALUES (1,'Si','3.1,','1000,')")
        db.execute('CREATE TABLE infodb (date TEXT)')
        db.execute("INSERT INTO infodb VALUES ('test')")
    node = create_node(client, 'xrd.match')
    def configure(p, revision):
        return client.post(f"/api/nodes/{node['id']}/actions/configure", json={'expected_revision':revision,'arguments':{'path':str(p)}})
    revision = client.get(f"/api/nodes/{node['id']}/document").json()['revision']
    result = configure(path, revision)
    assert result.status_code == 200, result.text
    value = result.json()['value']
    assert value['count'] == 1 and value['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    from oaw_xrd.library import validate_library
    validate_library(value)
    assert configure(tmp_path/'missing.sq',result.json()['revision']).status_code == 422
    assert client.get(f"/api/nodes/{node['id']}/document").json()['value'] == value
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO id VALUES (2,'O','2,','1000,')")
    with pytest.raises(ValueError, match='变更'):
        validate_library(value)


def test_library_search_scans_all_records_and_normalizes_intensity(tmp_path):
    root = Path(__file__).resolve().parents[2]
    python = root.parent/'XRD/.venv-xrd'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        pytest.skip('Scientific interpreter unavailable')
    script = r'''
import json, math, sys, sqlite3
sys.path.insert(0,sys.argv[1])
from matching import match_patterns
from library import inspect_library
path=sys.argv[2]
d=lambda angle:1.540593/(2*math.sin(math.radians(angle/2)))
with sqlite3.connect(path) as db:
 db.execute('CREATE TABLE id(id INTEGER,chemical_formula TEXT,dvalue TEXT,intensita TEXT)')
 db.execute('CREATE TABLE infodb(date TEXT)');db.execute("INSERT INTO infodb VALUES ('test')")
 # Last record is the true candidate: ensure there is no early database LIMIT.
 for i in range(30):db.execute('INSERT INTO id VALUES (?,?,?,?)',(i,'Na Cl',str(d(35+i*.01))+',','1000,'))
 db.execute('INSERT INTO id VALUES (?,?,?,?)',(100,'Si O2',f'{d(20)},{d(30)},','1000,500,'))
 db.execute('INSERT INTO id VALUES (?,?,?,?)',(101,'Si O2','bad','1,'))
points=[[10+i*.01,100+1000*math.exp(-((10+i*.01-20)/.07)**2)+500*math.exp(-((10+i*.01-30)/.07)**2)] for i in range(3001)]
inputs=[{'node_id':'p','revision':1,'value':{'kind':'pattern','filename':'synthetic','sha256':'p','metadata':{},'points':points}}, {'node_id':'lib','revision':1,'value':inspect_library(path)}]
o={'wavelength':1.540593,'tolerance_deg':.15,'prominence_fraction':.03,'smoothing_deg':.03,'min_peak_distance':.1,'reference_min_intensity':5,'library_top_n':1}
r=match_patterns(inputs,o)
assert r['library_search']['scanned']==32 and r['library_search']['invalid_records']==1
assert len(r['candidates'])==1 and r['candidates'][0]['node_id']=='lib:100'
assert r['candidates'][0]['score']>.99 and len(r['unexplained_peaks'])==0
assert max(p['intensity'] for p in r['candidates'][0]['peaks'])==100
assert r['candidates'][0]['metadata']['reference_kind']=='calculated'
o['library_elements']='Si O'
filtered=match_patterns(inputs,o)
assert filtered['library_search']['excluded_by_elements']==30
assert filtered['candidates'][0]['node_id']=='lib:100'
assert filtered['library_search']['invalid_record_ids']==['101']
# Multiple mounted slots are searched and retain separate provenance.
slot_doc=inputs[1]['value']
multi=[inputs[0],{**inputs[1],'value':{**slot_doc,'slots':[slot_doc,None,slot_doc]}}]
o['library_top_n']=2
multi_result=match_patterns(multi,o)
assert multi_result['library_search']['scanned']==64
assert {c['node_id'] for c in multi_result['candidates']}=={'lib:slot1:100','lib:slot3:100'}
assert len(multi_result['library_search']['libraries'])==2
o['library_top_n']=1
o['library_elements']='NotAnElement'
try:match_patterns(inputs,o)
except ValueError:pass
else:raise AssertionError('Invalid chemistry must not silently match everything')
o['library_elements']=''
o['wavelength']=1.0
assert not match_patterns(inputs,o)['candidates']
# QualX supplies library-local IDs only. Reuse OAW ranking without scanning the
# rest of the library, including two slots with the same database IDs.
import qualx
def recall(pattern,item,options,**kwargs):
 return ['100'],{'candidate_count':1,'elapsed_seconds':.01,'library_node_id':item['node_id']}
qualx.search_library=recall
o.update(wavelength=1.540593,library_engine='qualx',library_top_n=2)
fast=match_patterns(multi,o)
assert fast['library_search']['engine']=='qualx'
assert fast['library_search']['total_records']==64
assert fast['library_search']['scanned']==2
assert fast['library_search']['screened_candidates']==2
assert {c['node_id'] for c in fast['candidates']}=={'lib:slot1:100','lib:slot3:100'}
manual={'node_id':'manual','revision':1,'value':{'kind':'reference','filename':'manual','sha256':'r','metadata':{},'peaks':[{'d':d(20),'intensity':100,'hkl':[1,0,0]}]}}
qualx.search_library=lambda *a,**kw:([],{'candidate_count':0,'elapsed_seconds':.01})
empty=match_patterns(inputs+[manual],o)
assert [c['node_id'] for c in empty['candidates']]==['manual']
assert empty['library_search']['scanned']==0
qualx.search_library=lambda *a,**kw:(['999999'],{'candidate_count':1,'elapsed_seconds':.01})
try:match_patterns(inputs,o)
except ValueError as e:assert 'QualX' in str(e)
else:raise AssertionError('Foreign candidate IDs must not silently pass')
print('full scan, ranking, intensity normalization, invalid records and wavelength passed')
'''
    result = subprocess.run([str(python),'-c',script,str(root/'plugins/xrd/src/oaw_xrd'),str(tmp_path/'cod.sq')],capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr

def test_library_slots_preserve_existing_mounts(client, tmp_path):
    import sqlite3
    from oaw_xrd.library import expand_library_slots
    paths=[]
    for n in range(2):
        path=tmp_path/f'library{n}.sq'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE id(id INTEGER,chemical_formula TEXT,dvalue TEXT,intensita TEXT)')
            db.execute('INSERT INTO id VALUES (?,?,?,?)',(n,'Si','3,','1000,'))
            db.execute('CREATE TABLE infodb(date TEXT)')
        paths.append(path)
    node=create_node(client,'xrd.match')
    endpoint=f"/api/nodes/{node['id']}"
    def mount(slot,path):
        revision=client.get(endpoint+'/document').json()['revision']
        return client.post(endpoint+'/actions/configure',json={'expected_revision':revision,'arguments':{'path':str(path),'slot':slot}})
    assert mount(0,paths[0]).status_code==200
    response=mount(4,paths[1]);assert response.status_code==200,response.text
    doc=response.json()
    assert len(doc['value']['slots'])==6
    assert doc['value']['slots'][0]['path']==str(paths[0].resolve())
    assert doc['value']['slots'][4]['path']==str(paths[1].resolve())
    expanded=expand_library_slots([{'node_id':node['id'],'revision':doc['revision'],'value':doc['value']}])
    assert len(expanded)==2 and expanded[1]['node_id'].endswith(':slot5')
    assert mount(1,paths[0]).status_code==422
    assert mount(2,tmp_path/'absent.sq').status_code==422
    assert client.get(endpoint+'/document').json()==doc
