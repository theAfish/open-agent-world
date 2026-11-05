import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.hosting.gateway import create_gateway
from backend.hosting.packs import management_grant, pack_locks, retain_archive, stage_archives
from backend.hosting.worker import WorkerConfig, create_worker_app
from backend.tests.test_hosted_gateway import fixture_data, ORIGIN
from backend.tests.test_hosted_worker import config, headers
from backend.tests.test_pack_installation import artifact, isolate_modules


@pytest.mark.asyncio
async def test_admin_installs_through_gateway_and_cannot_delegate_authority_to_a_user(tmp_path):
    store, users, profiles, frontend, settings = fixture_data(tmp_path)
    store.set_role(users[0]['id'],'admin')
    profiles = (replace(profiles[0],pack_management=True),profiles[1])
    settings = replace(settings,profiles=profiles)
    workers = {p.upstream:create_worker_app(WorkerConfig(p.profile_id,p.user_id,p.application_id,p.mode,ORIGIN,p.secret,
        tmp_path/f'worker-{i}',agent_runtime='core.mock',pack_management=p.pack_management)) for i,p in enumerate(profiles)}
    calls = []
    async def dispatch(request):
        calls.append(request)
        origin = str(request.url.copy_with(path='',query=None)).rstrip('/')
        return await httpx.ASGITransport(app=workers[origin]).handle_async_request(request)
    gateway = create_gateway(settings,frontend,store=store,transport=httpx.MockTransport(dispatch))
    data = artifact()
    async with AsyncExitStack() as stack:
        for worker in workers.values():
            await stack.enter_async_context(worker.router.lifespan_context(worker))
        await stack.enter_async_context(gateway.app.router.lifespan_context(gateway.app))
        client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url=ORIGIN))
        url = '/api/packs/install'
        supplied = {'Origin':ORIGIN,'X-OAW-Instance':profiles[0].profile_id,'X-OAW-Pack-Install':'1','Content-Type':'application/vnd.oaw.pack'}
        client.cookies.set('oaw_session',store.create_session(users[0]['id']))
        response = await client.post(url,headers=supplied,content=data)
        assert response.status_code==201,response.text
        assert response.json()['versions'][0]['selected']
        digest = response.json()['versions'][0]['digest']
        assert (tmp_path/'pack-archives'/f'{digest}.oawpack').read_bytes()==data
        assert any('x-oaw-pack-authority' in request.headers for request in calls)
        before = len(calls)
        client.cookies.set('oaw_session',store.create_session(users[1]['id']))
        # The browser cannot supply a role/grant or select the admin's instance.
        forged = {**supplied,'X-OAW-Instance':profiles[1].profile_id,'X-OAW-Pack-Authority':management_grant(
            profiles[0].secret,profiles[0].profile_id,'POST',url),'X-OAW-Role':'admin'}
        assert (await client.post(url,headers=forged,content=data)).status_code==403
        assert len(calls)==before
        assert not list((tmp_path/'worker-1/packs').glob('versions/*'))
        assert (await client.post(url,headers={**forged,'Origin':'https://evil.example'},content=data)).status_code==403
        store.set_role(users[0]['id'],'user')
        client.cookies.set('oaw_session',store.create_session(users[0]['id']))
        assert (await client.get('/api/packs',headers={'Origin':ORIGIN})).status_code==403


def test_worker_grants_are_bound_to_operator_flag_method_path_and_owner(tmp_path):
    worker = config(tmp_path,pack_management=True)
    restarted = []
    with TestClient(create_worker_app(worker,restart=lambda:restarted.append(True))) as client:
        ordinary = {**headers(worker),'X-OAW-Pack-Install':'1'}
        assert client.get('/api/packs',headers=ordinary).status_code==403
        signed = {**ordinary,'X-OAW-Pack-Authority':management_grant(worker.secret,worker.profile_id,'GET','/api/packs')}
        assert client.get('/api/packs',headers=signed).status_code==200
        assert client.post('/api/packs/install',headers=signed).status_code==403
        assert client.get('/api/desktop/pick-folder',headers=signed).status_code==403
        project = {**ordinary,'X-OAW-Pack-Authority':management_grant(worker.secret,worker.profile_id,'POST','/api/packs/projects')}
        assert client.post('/api/packs/projects',headers=project).status_code==403
        restart = {**ordinary,'X-OAW-Restart':'1','X-OAW-Pack-Authority':management_grant(
            worker.secret,worker.profile_id,'POST','/api/application/restart')}
        assert client.post('/api/application/restart',headers=restart).status_code==202
        assert restarted==[True]
    disabled = replace(worker,data_root=tmp_path/'disabled',profile_id=str(uuid4()),pack_management=False)
    with TestClient(create_worker_app(disabled)) as client:
        forged = {**headers(disabled),'X-OAW-Pack-Authority':management_grant(disabled.secret,disabled.profile_id,'GET','/api/packs')}
        assert client.get('/api/packs',headers=forged).status_code==403


def test_hosted_workers_reject_legacy_runtime_execution_before_start(tmp_path):
    from backend.agents.mock import MockAgentRuntime
    from backend.errors import RuntimeUnavailableError
    class LegacyRuntime(MockAgentRuntime):
        supports_managed_execution=False
    worker=config(tmp_path)
    with TestClient(create_worker_app(worker)) as client:
        manager=client.app.state.services.run_manager
        with pytest.raises(RuntimeUnavailableError,match='updated connector'):
            manager.install_provider('core.mock',LegacyRuntime(manager.capability_provider))
        # The same legacy adapter remains usable under the personal policy.
        manager.require_managed_execution=False
        manager.install_provider('core.mock',LegacyRuntime(manager.capability_provider))


@pytest.mark.parametrize('card_type', ['xrd.match', 'atomsculptor.agent'])
def test_hosted_bundled_managed_runtimes_remain_creatable(tmp_path, card_type):
    worker=config(tmp_path)
    with TestClient(create_worker_app(worker)) as client:
        response=client.post('/api/nodes',headers=headers(worker),json={'type':card_type})
        assert response.status_code==201,response.text
        assert response.json()['type']==card_type


def test_approved_archive_is_prepared_and_loaded_in_a_separate_user_process(tmp_path):
    from backend.hosting.templates import export_template
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.loader import load_plugin_registry
    from backend.tests.test_deployments import prepare_source
    source = tmp_path/'author'
    prepared = prepare_source(source)
    data = artifact()
    pack = retain_archive(tmp_path/'cache',data)
    registry = load_plugin_registry(data_root=source,load_installed=False)
    PackInstallationManager(source,registry).install(data)
    from backend.main import create_app
    from backend.tests.test_deployments import settings
    with TestClient(create_app(settings(source))) as client:
        release = client.post('/api/deployments',json={'legion_id':prepared['legion']['id'],'name':'With approved Pack'})
        assert release.status_code==201,release.text
    template = asyncio.run(export_template(source,release.json()['id'],tmp_path/'template.json'))
    assert template['packs']==[{'id':'test.greeter','version':'0.1.0','sha256':pack.digest}]
    instance = tmp_path/'runtime'
    instance.mkdir()
    stage_archives(tmp_path/'cache',instance,template['packs'])
    worker = config(instance/'data',approved_packs=tuple(template['packs']))
    worker = replace(worker,mode='runtime')
    value = {**asdict(worker),'data_root':str(worker.data_root)}
    script = '''
import asyncio,json,sys
from dataclasses import replace
import pytest
from backend.hosting.worker import WorkerConfig,create_worker_app
from backend.hosting.templates import initialize_runtime
from backend.hosting.upgrades import migrate_profile
from fastapi.testclient import TestClient
value=json.loads(sys.stdin.readline())
config=WorkerConfig(**value['worker'])
asyncio.run(initialize_runtime(config,__import__('pathlib').Path(value['template'])))
manifest=config.data_root/'deployment.json'
original=manifest.read_bytes()
bad=replace(config,approved_packs=({**config.approved_packs[0],'sha256':'0'*64},))
with pytest.raises(ValueError,match='administrator-approved'):
 asyncio.run(migrate_profile(bad))
assert manifest.read_bytes()==original
asyncio.run(migrate_profile(config))
assert json.loads(manifest.read_text())['plugin_versions']['test.greeter']=='0.1.0'
with TestClient(create_worker_app(config)) as client:
 services=client.app.state.services
 assert services.plugins.has_plugin('test.greeter')
 assert services.pack_installations.status()['versions'][0]['loaded']
 print('approved-pack-loaded')
'''
    env = {**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2])}
    result = subprocess.run([sys.executable,'-c',script],input=json.dumps({'worker':value,'template':str(tmp_path/'template.json')})+'\n',
        text=True,capture_output=True,env=env,timeout=60)
    assert result.returncode==0,result.stderr
    assert 'approved-pack-loaded' in result.stdout


@pytest.mark.parametrize('fault',['digest','hardlink'])
def test_privileged_data_copy_rejects_tampered_or_linked_archives(tmp_path,fault):
    data = artifact()
    pack = retain_archive(tmp_path/'cache',data)
    path = tmp_path/'cache'/f'{pack.digest}.oawpack'
    if fault=='digest':
        path.write_bytes(data+b'changed')
    else:
        os.link(path,tmp_path/'linked')
    (tmp_path/'instance').mkdir()
    with pytest.raises(ValueError,match='verification|regular'):
        stage_archives(tmp_path/'cache',tmp_path/'instance',[{'id':'test.greeter','version':'0.1.0','sha256':pack.digest}])


@pytest.mark.asyncio
async def test_store_proxy_retains_exact_verified_archive_without_importing_pack_code(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from backend.hosting.packs import acquire_for_gateway
    from backend.packs.marketplace import MarketplaceClient
    from backend.tests.test_marketplace import FakeMarketplace
    fake=FakeMarketplace()
    client=MarketplaceClient('https://marketplace.test',transport=httpx.MockTransport(fake))
    monkeypatch.setattr('backend.packs.marketplace.MarketplaceClient',lambda *args,**kwargs:client)
    request=Request({'type':'http','method':'POST','path':'/api/store/packs/test.greeter/versions/0.1.0/install',
        'headers':[],'scheme':'https','server':('oaw.example',443),'query_string':b''})
    gateway=SimpleNamespace(settings=SimpleNamespace(accounts_path=tmp_path/'accounts.sqlite3'))
    assert await acquire_for_gateway(gateway,request)==fake.data
    assert (tmp_path/'pack-archives'/(fake.metadata['sha256']+'.oawpack')).read_bytes()==fake.data
    assert not any(name.startswith('oaw_pack_test_greeter') for name in sys.modules)


@pytest.mark.asyncio
async def test_staged_packs_install_dependencies_before_consumers(tmp_path):
    from copy import deepcopy
    from backend.hosting.packs import install_staged
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.builtin import create_builtin_registry
    from backend.tests.test_pack_installation import MANIFEST
    producer=deepcopy(MANIFEST);producer['id']='test.z'
    consumer=deepcopy(MANIFEST);consumer['id']='test.a'
    consumer['dependencies']['packs']=[{'id':'test.z','version':'>=0.1.0'}]
    locks=[]
    for manifest in (consumer,producer):
        pack=retain_archive(tmp_path/'cache',artifact(manifest))
        locks.append({'id':manifest['id'],'version':manifest['version'],'sha256':pack.digest})
    instance=tmp_path/'instance';instance.mkdir()
    stage_archives(tmp_path/'cache',instance,locks)
    worker=config(instance/'data',approved_packs=tuple(locks))
    await install_staged(worker,locks,create_builtin_registry())
    assert set(PackInstallationManager(worker.data_root).selected())=={'test.a','test.z'}


def test_hosted_cli_restart_exits_for_systemd_and_reloads_the_installed_pack(tmp_path):
    import socket,time
    from backend.hosting.worker import mint_worker_token
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    worker=replace(config(tmp_path/'data',pack_management=True),public_origin=origin)
    path=tmp_path/'worker.json'
    path.write_text(json.dumps({**asdict(worker),'data_root':'data'}))
    root=Path(__file__).resolve().parents[2]
    frontend=tmp_path/'frontend';frontend.mkdir();(frontend/'index.html').write_text('<html>Hosted fixture</html>')
    command=[sys.executable,'-B','scripts/hosted.py','worker','--config',str(path),'--port',str(port),'--frontend',str(frontend)]
    env={**os.environ,'PYTHONPATH':str(root),'PYTHONIOENCODING':'utf-8'}
    env.pop('OAW_HOSTED_SERVICES_FACTORY',None)
    env.pop('OPEN_AGENT_WORLD_CORE_ONLY',None)
    def authority(method,path):
        return {'Origin':origin,'X-OAW-Pack-Install':'1','X-OAW-Restart':'1',
            'Authorization':'Bearer '+mint_worker_token(worker.secret,worker.profile_id,worker.user_id),
            'X-OAW-Pack-Authority':management_grant(worker.secret,worker.profile_id,method,path)}
    with (tmp_path/'server.log').open('w',encoding='utf-8') as log,httpx.Client(base_url=origin,trust_env=False,timeout=10) as client:
        for phase in ('install','reload'):
            process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+40
                while time.monotonic()<deadline and process.poll() is None:
                    try:
                        if client.get('/health/ready').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.1)
                else:pytest.fail('Hosted worker did not start: '+(tmp_path/'server.log').read_text(encoding='utf-8')[-4000:])
                if phase=='install':
                    response=client.post('/api/packs/install',content=artifact(),headers={
                        **authority('POST','/api/packs/install'),'Content-Type':'application/vnd.oaw.pack'})
                    assert response.status_code==201,response.text
                    response=client.post('/api/application/restart',headers=authority('POST','/api/application/restart'))
                    assert response.status_code==202,response.text
                    assert process.wait(timeout=15)==75
                else:
                    response=client.get('/api/packs',headers=authority('GET','/api/packs'))
                    assert response.status_code==200,response.text
                    assert response.json()['versions'][0]['loaded']
            finally:
                if process.poll() is None:
                    process.terminate();process.wait(timeout=15)
