import asyncio
from contextlib import asynccontextmanager
import json
import os
import sys

import pytest

from backend.agents.execution import NativeRuntimeProcess
from backend.agents.execution_transport import FRAME_LIMIT, RelayRuntimeExecution, execution_worker_argv
from backend.errors import ResourceValidationError, RuntimeUnavailableError


@asynccontextmanager
async def relay(tmp_path):
    process = await asyncio.create_subprocess_exec(*execution_worker_argv(sys.executable),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=FRAME_LIMIT,
        **({'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True}))
    host=RelayRuntimeExecution(NativeRuntimeProcess(process))
    try:
        await host.initialize(str(tmp_path/'project'),str(tmp_path/'home'))
        yield host
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_real_relay_duplex_process_and_scoped_files(tmp_path):
    async with relay(tmp_path) as host:
        staged=await host.write_file('input/data.bin',b'\0binary\xff')
        assert await host.read_file(staged)==b'\0binary\xff'
        with pytest.raises(ResourceValidationError):
            await host.read_file('../outside')
        script='import sys;print("ready",flush=True);print(sys.stdin.readline().strip(),flush=True)'
        process=await host.spawn([sys.executable,'-I','-u','-c',script],env=dict(os.environ))
        assert (await process.readline()).rstrip(b'\r\n')==b'ready'
        await process.write(b'protocol-neutral\n')
        assert (await process.readline()).rstrip(b'\r\n')==b'protocol-neutral'
        assert await process.wait()==0


@pytest.mark.asyncio
async def test_real_relay_streams_large_files_and_discards_failed_transfers(tmp_path, monkeypatch):
    import hashlib

    chunk = b'x' * (256 * 1024)

    async def content():
        for _ in range(132):  # 33 MiB, larger than the unary file limit.
            yield chunk

    async with relay(tmp_path) as host:
        target = await host.write_file_stream('input/large.xlsx', content())
        path = tmp_path / 'project/input/large.xlsx'
        assert target == str(path)
        expected = hashlib.sha256()
        for _ in range(132):
            expected.update(chunk)
        with path.open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == expected.hexdigest()
        original = host.request
        replies = []

        async def request(method, **params):
            result = await original(method, **params)
            if method == 'download_file':
                replies.append((params['operation'], len(result.get('data', ''))))
            else:
                assert method != 'read_file', 'Large output must never use the unary bytes path'
            return result

        monkeypatch.setattr(host, 'request', request)
        returned = hashlib.sha256()
        async for block in host.read_file_stream(target):
            assert len(block) <= len(chunk)
            returned.update(block)
        assert returned.hexdigest() == expected.hexdigest()
        assert replies[0] == ('download_info', 0) and replies[-1] == ('download_chunk', 0)
        assert max(size for _, size in replies) <= 4 * ((len(chunk) + 2) // 3)
        monkeypatch.setattr(host, 'request', original)

        async def revoked():
            yield b'partial'
            raise PermissionError('attachment access revoked')

        with pytest.raises(PermissionError, match='revoked'):
            await host.write_file_stream('input/revoked.xlsx', revoked())
        assert not (path.parent / 'revoked.xlsx').exists()
        assert not list(path.parent.glob('.oaw-upload-*.part'))
        with pytest.raises(ResourceValidationError):
            await host.write_file_stream('../outside', content())
        with pytest.raises(ResourceValidationError, match='already exists'):
            await host.write_file_stream('input/large.xlsx', content())


@pytest.mark.asyncio
async def test_partial_frame_failure_blocks_abort_and_queued_requests_but_still_closes_transport(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def content():
        yield b'file content'

    async with relay(tmp_path) as host:
        original_write = host.transport.write
        transport = host.transport

        async def interrupted_write(data):
            operation = json.loads(data)['params'].get('operation')
            operations.append(operation)
            if operation == 'upload_chunk':
                await original_write(data[:20])
                entered.set()
                await release.wait()
                raise OSError('SDK stdin response was lost')
            await original_write(data)

        monkeypatch.setattr(transport, 'write', interrupted_write)
        upload = asyncio.create_task(host.write_file_stream('input/test.xlsx', content()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            queued = asyncio.create_task(host.read_file('another-file'))
            await asyncio.sleep(0)
            assert len(host.pending) == 2
            release.set()
            failed_upload, failed_queued = await asyncio.gather(upload, queued, return_exceptions=True)
            assert isinstance(failed_upload, RuntimeUnavailableError)
            assert 'outcome is uncertain' in str(failed_upload)
            assert failed_queued is failed_upload
            assert isinstance(failed_upload.__cause__, OSError)
            assert operations == ['upload_begin', 'upload_chunk']
            assert not host.pending
        finally:
            release.set()
            await asyncio.gather(upload, return_exceptions=True)
    assert host.closed and transport._closed
    assert host.failure is failed_upload  # EOF during owned cleanup cannot replace the first failure.


@pytest.mark.asyncio
async def test_reader_failure_during_pending_write_is_observed_and_preserved(tmp_path, monkeypatch):
    import gc

    entered, release = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unobserved = []
    loop.set_exception_handler(lambda _, context: unobserved.append(context))
    try:
        async with relay(tmp_path) as host:
            async def interrupted_write(data):
                entered.set()
                await release.wait()
                raise OSError('write failed after reader reached EOF')

            monkeypatch.setattr(host.transport, 'write', interrupted_write)
            request = asyncio.create_task(host.read_file('input.xlsx'))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await host.transport.close()
                await host.reader
                first_failure = host.failure
                release.set()
                with pytest.raises(RuntimeUnavailableError) as error:
                    await request
                assert error.value is first_failure
                assert not host.pending
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)
        del request
        gc.collect()
        await asyncio.sleep(0)
        assert not unobserved
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_private_http_tool_bridge_round_trip(tmp_path):
    calls=[]
    async def tool(reader,writer):
        try:
            header=await reader.readuntil(b'\r\n\r\n')
            assert b'authorization: Bearer run-fixture' in header
            length=int(next(line.split(b':',1)[1] for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')))
            calls.append(json.loads(await reader.readexactly(length)))
            body=b'{"tool":"ok"}'
            writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
            await writer.drain()
        finally:
            writer.close();await writer.wait_closed()
    server=await asyncio.start_server(tool,'127.0.0.1',0)
    try:
        async with relay(tmp_path) as host:
            local=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp'
            forwarded=await host.forward_http(local)
            assert forwarded!=local
            script='import urllib.request;request=urllib.request.Request('+repr(forwarded)+',data=b\'{"method":"tools/list"}\',headers={"Authorization":"Bearer run-fixture","Content-Type":"application/json"});print(urllib.request.urlopen(request,timeout=5).read().decode(),flush=True)'
            process=await host.spawn([sys.executable,'-I','-u','-c',script],env=dict(os.environ))
            assert json.loads(await process.readline())=={'tool':'ok'}
            assert await process.wait()==0
            assert calls==[{'method':'tools/list'}]
    finally:
        server.close();await server.wait_closed()


@pytest.mark.asyncio
async def test_remote_peer_cannot_select_an_arbitrary_host_url(tmp_path, monkeypatch):
    import httpx
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail('A forged proxy registration reached the host network')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    async with relay(tmp_path) as host:
        async def reply(method, **params):
            calls.append((method,params))
        monkeypatch.setattr(host, 'request', reply)
        await host._forward_http({'proxy_id':'forged','request_id':'request',
            'url':'http://169.254.169.254/latest/meta-data/','data':'','headers':{}})
        host.proxies['registered'] = {'url':'http://127.0.0.1:18081/mcp','path_prefix':False,'stream':False}
        await host._forward_http({'proxy_id':'registered','request_id':'malformed',
            'data':'','headers':[]})
        await host._forward_http({'proxy_id':'registered','request_id':'query',
            'query':'route=other','data':'','headers':{}})
        assert [(method,p['request_id'],p['status']) for method,p in calls] == [
            ('http_response','request',502),('http_response','malformed',502),('http_response','query',502)]


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['/private','/models-escape/chat','//169.254.169.254/latest',
    '/models/%2e%2e/private','/models/../private','/models/chat?target=http://evil.example','/models/\\private'])
async def test_forwarded_model_paths_cannot_escape_registered_prefix(tmp_path,monkeypatch,path):
    import httpx
    def forbidden(*args,**kwargs):
        pytest.fail('An escaped path reached the host network')
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    async with relay(tmp_path) as host:
        host.proxies['model']={'url':'http://127.0.0.1:18081/models','path_prefix':True,'stream':True}
        calls=[]
        async def reply(method,**params):calls.append((method,params))
        monkeypatch.setattr(host,'request',reply)
        await host._forward_http({'proxy_id':'model','request_id':'forged','path':path,'data':'','headers':{}})
        assert [method for method,_ in calls]==['http_start','http_end']
        assert calls[0][1]['status']==502
