import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
import json

import httpx
import pytest

from backend.agents.execution import NativeRuntimeExecutionProvider, RuntimeExecutionBroker, RuntimeExecutionRequest
from backend.agents.models import RuntimeModelConnection
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.tests.test_runtime_execution_relay import relay


@asynccontextmanager
async def upstream(handler):
    tasks = set()
    async def accept(reader, writer):
        task = asyncio.current_task();tasks.add(task)
        try:
            raw = (await reader.readuntil(b'\r\n\r\n')).decode()
            first, *pairs = raw.rstrip('\r\n').split('\r\n')
            headers = {key.lower(): value.strip() for key,value in (line.split(':',1) for line in pairs)}
            body = json.loads(await reader.readexactly(int(headers['content-length'])))
            await handler(first, headers, body, writer)
        finally:
            tasks.discard(task);writer.close()
            with suppress(OSError):await writer.wait_closed()
    server = await asyncio.start_server(accept, '127.0.0.1', 0)
    try:yield f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    finally:
        server.close()
        for task in tuple(tasks):task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await server.wait_closed()


def request(tmp_path):
    return RuntimeExecutionRequest('agent','run',tmp_path/'project',tmp_path/'home')


@pytest.mark.asyncio
@pytest.mark.parametrize('remote',[False,True])
async def test_execution_scoped_model_streams_without_exporting_upstream_key(tmp_path,remote):
    release=asyncio.Event();seen=[]
    async def handle(first,headers,body,writer):
        seen.append((first,headers,body))
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\ndata: first\n\n');await writer.drain()
        await release.wait()
        writer.write(b'data: last\n\n');await writer.drain()
    async with upstream(handle) as base:
        raw=RuntimeModelConnection('openai','chosen-model',base+'/v1','stored-secret-fixture')
        @asynccontextmanager
        async def provider():
            if remote:
                async with relay(tmp_path) as execution:
                    class Provider:
                        async def open(self, _):return execution
                    yield Provider()
            else:yield NativeRuntimeExecutionProvider()
        async with provider() as transport:
            broker=RuntimeExecutionBroker(transport,resolve_model=lambda _:raw)
            async with broker.open(request(tmp_path)) as execution:
                async with execution.model_connection('oaw:default') as model:
                    assert model.api_key != raw.api_key and raw.api_key not in repr(model)
                    assert raw.api_key not in execution.environment.values()
                    async with httpx.AsyncClient(trust_env=False) as client:
                        async with client.stream('POST',model.base_url+'/chat/completions',
                                json={'model':model.model_id,'stream':True},headers={'Authorization':'Bearer '+model.api_key}) as response:
                            chunks=response.aiter_bytes()
                            assert response.status_code==200 and response.headers['content-type']=='text/event-stream'
                            first=await asyncio.wait_for(anext(chunks),3)
                            assert b'first' in first and b'last' not in first
                            release.set()
                            remaining=b''.join([chunk async for chunk in chunks])
                            assert b'last' in remaining
                    assert seen[0][0]=='POST /v1/chat/completions HTTP/1.1'
                    assert seen[0][1]['authorization']=='Bearer '+raw.api_key


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter,path,auth_header',[('openai','/chat/completions','Authorization'),('anthropic','/v1/messages?beta=true','x-api-key')])
async def test_model_credentials_rotate_and_disabled_connections_are_revoked(tmp_path,adapter,path,auth_header):
    received=[];targets=[]
    async def handle(first,headers,body,writer):
        targets.append(first)
        received.append(headers)
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}');await writer.drain()
    async with upstream(handle) as base:
        current=[RuntimeModelConnection(adapter,'selected',base+('/v1' if adapter=='openai' else ''),'old-key')]
        def resolve(_):
            if current[0] is None:raise ResourceValidationError('disabled')
            return current[0]
        broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=resolve)
        async with broker.open(request(tmp_path)) as execution:
            async with execution.model_connection('oaw:model:fixture') as model:
                headers={auth_header:('Bearer ' if adapter=='openai' else '')+model.api_key}
                if adapter=='anthropic':headers['anthropic-beta']='interleaved-thinking-2025-05-14'
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.post(model.base_url+path,headers=headers,json={'model':'selected'})).status_code==200
                    current[0]=replace(current[0],api_key='rotated-key')
                    assert (await client.post(model.base_url+path,headers=headers,json={'model':'selected'})).status_code==200
                    assert received[-1][auth_header.lower()]==('Bearer ' if adapter=='openai' else '')+'rotated-key'
                    current[0]=None
                    assert (await client.post(model.base_url+path,headers=headers,json={'model':'selected'})).status_code==403
                    assert len(received)==2
                    if adapter=='anthropic':
                        assert targets[0]=='POST /v1/messages?beta=true HTTP/1.1'
                        assert received[0]['anthropic-beta']=='interleaved-thinking-2025-05-14'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,expected',[('token',401),('path',404),('model',400),('provider-error',401),('redirect',502)])
async def test_proxy_rejects_route_escalation_and_hides_provider_errors(tmp_path,fault,expected):
    calls=[]
    async def handle(first,headers,body,writer):
        calls.append(first)
        status=302 if fault=='redirect' else 401
        data=b'provider echoed stored-secret-fixture'
        writer.write(f'HTTP/1.1 {status} Error\r\nLocation: http://127.0.0.1:1/leak\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n'.encode()+data);await writer.drain()
    async with upstream(handle) as base:
        raw=RuntimeModelConnection('openai','selected',base+'/v1','stored-secret-fixture')
        broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=lambda _:raw)
        async with broker.open(request(tmp_path)) as execution:
            async with execution.model_connection('oaw:default') as model:
                headers={'Authorization':'Bearer '+('wrong' if fault=='token' else model.api_key)}
                path='/models' if fault=='path' else '/chat/completions'
                async with httpx.AsyncClient(trust_env=False) as client:
                    response=await client.post(model.base_url+path,headers=headers,json={'model':'expensive-unselected' if fault=='model' else 'selected'})
                assert response.status_code==expected
                assert raw.api_key not in response.text and 'location' not in response.headers
                assert len(calls)==(1 if fault in ('provider-error','redirect') else 0)


@pytest.mark.asyncio
async def test_execution_cleanup_revokes_a_forgotten_model_context(tmp_path):
    raw=RuntimeModelConnection('openai','model','https://example.invalid/v1','stored-secret-fixture')
    broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=lambda _:raw)
    async with broker.open(request(tmp_path)) as execution:
        context=execution.model_connection('oaw:default')
        model=await context.__aenter__()
        proxy=next(iter(broker.active['agent']['models']))
    try:
        assert proxy.closed and not proxy.server.is_serving()
        async with httpx.AsyncClient(trust_env=False,timeout=.5) as client:
            with pytest.raises((httpx.ConnectError,httpx.ConnectTimeout)):
                await client.post(model.base_url+'/chat/completions')
        with pytest.raises(RuntimeUnavailableError):
            async with execution.model_connection('oaw:default'):pytest.fail('Ended execution acquired a model')
    finally:
        await context.__aexit__(None,None,None)


@pytest.mark.asyncio
async def test_tokens_are_isolated_and_closing_execution_cancels_active_model_requests(tmp_path):
    started=asyncio.Event()
    async def handle(first,headers,body,writer):
        started.set()
        await asyncio.Event().wait()
    async with upstream(handle) as base:
        raw=RuntimeModelConnection('openai','selected',base+'/v1','stored-secret-fixture')
        broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=lambda _:raw)
        async with broker.open(request(tmp_path)) as first, broker.open(replace(request(tmp_path),node_id='other',operation_id='other-run')) as second:
            async with first.model_connection('oaw:default') as model, second.model_connection('oaw:default') as other:
                async with httpx.AsyncClient(trust_env=False) as client:
                    url=model.base_url+'/chat/completions'
                    response=await client.post(url,headers={'Authorization':'Bearer '+other.api_key},json={})
                    assert response.status_code==401 and not started.is_set()
                    pending=asyncio.create_task(client.post(url,headers={'Authorization':'Bearer '+model.api_key},json={}))
                    try:
                        await asyncio.wait_for(started.wait(),3)
                        proxy=next(iter(broker.active['agent']['models']))
                        await asyncio.wait_for(broker.close_operation('run'),3)
                        assert proxy.closed and not proxy.tasks
                        with pytest.raises(httpx.RemoteProtocolError):await asyncio.wait_for(pending,3)
                    finally:
                        pending.cancel()
                        await asyncio.gather(pending,return_exceptions=True)


@pytest.mark.asyncio
async def test_unsupported_adapter_and_changed_endpoint_fail_without_fallback(tmp_path):
    current=[RuntimeModelConnection('gemini','selected','https://provider.example','stored-secret')]
    broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=lambda _:current[0])
    async with broker.open(request(tmp_path)) as execution:
        with pytest.raises(ResourceValidationError,match='OpenAI or Anthropic'):
            async with execution.model_connection('oaw:default'):pytest.fail('Unsupported protocol accepted')
        current[0]=replace(current[0],adapter='openai')
        async with execution.model_connection('oaw:default') as model:
            current[0]=replace(current[0],base_url='https://changed.example')
            async with httpx.AsyncClient(trust_env=False) as client:
                response=await client.post(model.base_url+'/chat/completions',json={},headers={'Authorization':'Bearer '+model.api_key})
                assert response.status_code==403


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter',['openai','anthropic'])
async def test_keyless_local_models_keep_upstream_auth_disabled(tmp_path,adapter):
    async def handle(first,headers,body,writer):
        assert 'authorization' not in headers and 'x-api-key' not in headers
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}');await writer.drain()
    async with upstream(handle) as base:
        raw=RuntimeModelConnection(adapter,'local-model',base,None)
        broker=RuntimeExecutionBroker(NativeRuntimeExecutionProvider(),resolve_model=lambda _:raw)
        async with broker.open(request(tmp_path)) as execution:
            async with execution.model_connection('oaw:default') as model:
                path='/chat/completions' if adapter=='openai' else '/v1/messages'
                headers={'Authorization':'Bearer '+model.api_key} if adapter=='openai' else {'x-api-key':model.api_key}
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.post(model.base_url+path,json={},headers=headers)).status_code==200
