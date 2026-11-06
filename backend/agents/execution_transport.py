"""Provider-neutral relay protocol over a privately owned duplex process."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from functools import lru_cache
import json
from pathlib import Path
from uuid import uuid4
import zlib
from urllib.parse import unquote

from backend.agents.execution import (
    FILE_LIMIT, RuntimeExecution, RuntimeProcess, command_arguments, loopback_endpoint,
)
from backend.errors import ResourceValidationError, RuntimeUnavailableError

FRAME_LIMIT = 48 * 1024 * 1024
STREAM_LIMIT = 16 * 1024 * 1024
HTTP_REQUEST_LIMIT = 1024 * 1024
HTTP_RESPONSE_LIMIT = 8 * 1024 * 1024

_BOOTSTRAP = '''
import base64,importlib.abc,importlib.util,json,sys,zlib
sources=json.loads(zlib.decompress(base64.b85decode(sys.argv[1])))
class Loader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in sources:return importlib.util.spec_from_loader(fullname,self,is_package=fullname in {'backend','backend.agents','backend.sandbox'})
 def create_module(self,spec):return None
 def exec_module(self,module):exec(compile(sources[module.__name__],'<oaw-execution-relay>','exec'),module.__dict__)
sys.meta_path.insert(0,Loader())
from backend.agents.execution_worker import main
main()
'''


@lru_cache(maxsize=1)
def execution_worker_argv(python='/usr/bin/python3'):
    """Ship reviewed stdlib source, never host imports, keys or file paths."""
    root = Path(__file__).resolve().parents[1]
    sources = {'backend':'','backend.agents':'','backend.sandbox':''}
    for name in ['errors','file_transfer','agents.execution','agents.execution_worker',
            'sandbox.models','sandbox.materialization','sandbox.files','sandbox.environment',
            'sandbox.file_policy','sandbox.streaming','sandbox.transfers']:
        sources['backend.'+name] = (root/Path(*name.split('.'))).with_suffix('.py').read_text(encoding='utf-8')
    # Keep the stdlib bootstrap below Windows' 32767-character argv bound.
    encoded = base64.b85encode(zlib.compress(json.dumps(sources).encode(), level=9)).decode()
    return (python,'-I','-S','-u','-c',_BOOTSTRAP,encoded)


class _Bytes:
    def __init__(self):
        self.buffer = bytearray()
        self.ready = asyncio.Event()
        self.eof = False
        self.error = None

    def feed(self, data):
        if data is None:
            self.eof = True
        else:
            if len(self.buffer)+len(data)>STREAM_LIMIT:
                raise RuntimeUnavailableError('Runtime output exceeded its bounded consumer buffer')
            self.buffer.extend(data)
        self.ready.set()

    async def take(self, size=None, *, line=False):
        if not line and (type(size) is not int or size <= 0):
            raise ResourceValidationError('Runtime stream reads require a positive byte count')
        while True:
            if self.error:
                raise self.error
            end = self.buffer.find(b'\n')+1 if line else min(size,len(self.buffer))
            if end or self.eof:
                end = end or len(self.buffer)
                data = bytes(self.buffer[:end]);del self.buffer[:end]
                return data
            self.ready.clear()
            await self.ready.wait()


class RelayRuntimeProcess(RuntimeProcess):
    def __init__(self, session, process_id):
        self.session, self.process_id = session, process_id
        self.stdout, self.stderr = _Bytes(), _Bytes()
        self.exit = asyncio.get_running_loop().create_future()
        self.exit.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        self.closed = False

    async def write(self, data):
        await self.session.request('stdin',process_id=self.process_id,data=base64.b64encode(data).decode())

    async def read(self, size=16384):
        return await self.stdout.take(size)

    async def readline(self):
        return await self.stdout.take(line=True)

    async def read_stderr(self, size=8192):
        return await self.stderr.take(size)

    async def wait(self):
        return await asyncio.shield(self.exit)

    async def close(self):
        if not self.closed:
            await self.session.request('close_process',process_id=self.process_id)
            self.closed = True


class RelayRuntimeExecution(RuntimeExecution):
    remote = True

    def __init__(self, transport: RuntimeProcess):
        self.transport = transport
        self.pending = {}
        self.processes = {}
        self.proxies = {}
        self.forwarding = set()
        self.closed = False
        self.closing = False
        self.failure = None
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())

    async def initialize(self, workspace, home, *, environment=None, variables=None):
        value = await self.request('initialize',workspace=workspace,home=home,environment=environment or {},variables=variables or {})
        self.workspace,self.home = value['workspace'],value['home']
        self.environment=value['environment']
        return self

    async def _drain_stderr(self):
        while await self.transport.read_stderr():
            pass  # Diagnostics may contain private configuration; never publish them.

    async def request(self, method, **params):
        if self.closed or self.closing and method!='close':
            raise RuntimeUnavailableError('Runtime execution is closed')
        if self.failure is not None:
            raise self.failure
        if len(self.pending)>=128:
            raise RuntimeUnavailableError('Runtime execution request limit exceeded')
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
        line = json.dumps({'id':request_id,'method':method,'params':params},ensure_ascii=True).encode()+b'\n'
        if len(line)>FRAME_LIMIT:
            raise ResourceValidationError('Runtime execution request exceeds its frame limit')
        self.pending[request_id] = future
        try:
            async with self._write_lock:
                if self.failure is not None:
                    raise self.failure
                try:
                    await self.transport.write(line)
                except (Exception, asyncio.CancelledError) as error:
                    # A partial JSON frame cannot be followed by another request,
                    # including an upload abort. Only owned transport cleanup is safe.
                    self._fail(RuntimeUnavailableError(
                        'Runtime transport write failed; request outcome is uncertain and was not resubmitted'))
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise self.failure from error
            return await asyncio.wait_for(future,30)
        except TimeoutError as error:
            raise RuntimeUnavailableError('Runtime request outcome is uncertain; it was not resubmitted') from error
        finally:
            self.pending.pop(request_id,None)
            if not future.done():future.cancel()

    def _fail(self, error):
        if self.failure is not None:return
        self.failure = error
        for future in self.pending.values():
            if not future.done():future.set_exception(error)
        for process in self.processes.values():
            for stream in (process.stdout,process.stderr):
                stream.error=error;stream.ready.set()
            if not process.exit.done():process.exit.set_exception(error)

    async def _read(self):
        failure = RuntimeUnavailableError('Runtime relay ended before cleanup was confirmed')
        try:
            while line := await self.transport.readline():
                if len(line)>FRAME_LIMIT:raise ValueError('Runtime relay frame exceeds limit')
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('Runtime relay messages must be objects')
                if 'event' in value:
                    kind = value['event']
                    if kind=='http':
                        if len(self.forwarding)>=16:raise ValueError('Tool proxy request limit exceeded')
                        task=asyncio.create_task(self._forward_http(value))
                        self.forwarding.add(task)
                        task.add_done_callback(self._forwarded)
                        continue
                    process = self.processes.get(value.get('process_id'))
                    if process is None:raise ValueError('Unknown runtime process identity')
                    if kind in {'stdout','stderr'}:
                        data=value['data']
                        getattr(process,kind).feed(base64.b64decode(data,validate=True) if data is not None else None)
                    elif kind=='exit' and not process.exit.done():
                        process.exit.set_result(value['returncode'])
                elif (future:=self.pending.get(value.get('id'))) is not None and not future.done():
                    if 'error' in value:
                        error = value['error']
                        cls = {'FileNotFoundError':FileNotFoundError,'FileExistsError':FileExistsError,
                            'PermissionError':PermissionError,'SandboxSecurityError':ResourceValidationError,
                            'SandboxValidationError':ResourceValidationError,'ResourceValidationError':ResourceValidationError}.get(error.get('type'),RuntimeUnavailableError)
                        future.set_exception(cls(error.get('message','Runtime request failed')))
                    else:future.set_result(value['result'])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure=RuntimeUnavailableError('Runtime relay protocol failed: '+str(error))
        finally:
            self._fail(failure)

    async def spawn(self, argv, *, env):
        command_arguments(argv,env)
        process_id=uuid4().hex
        process=RelayRuntimeProcess(self,process_id)
        self.processes[process_id]=process
        try:
            await self.request('spawn',process_id=process_id,argv=list(argv),env=dict(env))
            return process
        except BaseException:
            self.processes.pop(process_id,None)
            raise

    def _forwarded(self, task):
        self.forwarding.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._fail(RuntimeUnavailableError('Runtime tool forwarding failed'))

    async def read_file(self, path, *, root='workspace', max_bytes=FILE_LIMIT):
        if type(max_bytes) is not int or not 0<max_bytes<=FILE_LIMIT:
            raise ResourceValidationError('Runtime file limit must be 1..32 MiB')
        value=await self.request('read_file',path=path,root=root,max_bytes=max_bytes)
        data=base64.b64decode(value['data'],validate=True)
        if len(data)>max_bytes:raise ResourceValidationError('Runtime file exceeds its limit')
        return data

    async def write_file(self, path, data, *, root='workspace'):
        if not isinstance(data,bytes) or len(data)>FILE_LIMIT:
            raise ResourceValidationError('Runtime file must contain at most 32 MiB of bytes')
        value=await self.request('write_file',path=path,root=root,data=base64.b64encode(data).decode())
        return value['path']

    async def _file_upload(self, operation, path, *, root='workspace', **options):
        return await self.request('upload_file', operation=operation, path=path, root=root, **options)

    async def _file_download(self, operation, path, *, root='workspace', **options):
        return await self.request('download_file', operation=operation, path=path, root=root, **options)

    async def forward_http(self, url, *, path_prefix=False, stream=False):
        target=loopback_endpoint(url)
        if type(path_prefix) is not bool or type(stream) is not bool:
            raise ResourceValidationError('HTTP forwarding options must be booleans')
        proxy_id=uuid4().hex
        self.proxies[proxy_id]={'url':url,'path_prefix':path_prefix,'stream':stream}
        value=await self.request('forward_http',proxy_id=proxy_id,path=target.path or '/',path_prefix=path_prefix,stream=stream)
        loopback_endpoint(value['url'])
        return value['url']

    async def _forward_http(self, value):
        import httpx
        request_id=value.get('request_id')
        streaming=False
        started=False
        try:
            # The remote peer selects only an opaque registration, never a host URL.
            registration=self.proxies[value['proxy_id']]
            target=loopback_endpoint(registration['url'])
            streaming=registration['stream']
            path=value.get('path',target.path or '/')
            if (not isinstance(path,str) or not path.startswith('/') or path.startswith('//')
                    or any(c in path for c in ('\\','?','#')) or any(ord(c)<32 for c in path)
                    or '..' in unquote(path).split('/')):
                raise ValueError('Invalid forwarded path')
            expected=target.path or '/'
            if (path != expected and (not registration['path_prefix']
                    or not path.startswith(expected.rstrip('/')+'/'))):
                raise ValueError('Forwarded path escaped its registration')
            query=value.get('query','')
            if (not isinstance(query,str) or len(query)>4096
                    or query and not registration['path_prefix']
                    or any(ord(c)<32 or c in ('#','\\') for c in query)):
                raise ValueError('Invalid forwarded query')
            url=target._replace(path=path,query=query).geturl()
            body=base64.b64decode(value['data'],validate=True)
            if len(body)>(FILE_LIMIT if streaming else HTTP_REQUEST_LIMIT):raise ValueError('HTTP proxy request exceeds limit')
            source = value['headers']
            if not isinstance(source, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in source.items()):
                raise ValueError('Invalid tool proxy headers')
            headers={k:v for k,v in source.items()
                if k.lower() not in {'host','connection','content-length','transfer-encoding'}}
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=600) as client:
                async with client.stream('POST',url,content=body,headers=headers) as response:
                    if streaming:
                        media=response.headers.get('content-type','application/json')
                        await self.request('http_start',request_id=request_id,status=response.status_code,content_type=media)
                        started=True
                        size=0
                        async for chunk in response.aiter_bytes():
                            size+=len(chunk)
                            if size>HTTP_RESPONSE_LIMIT:raise ValueError('HTTP proxy response exceeds limit')
                            for offset in range(0,len(chunk),16384):
                                reply=await self.request('http_chunk',request_id=request_id,data=base64.b64encode(chunk[offset:offset+16384]).decode())
                                if not reply['accepted']:return
                        await self.request('http_end',request_id=request_id)
                        return
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data)>HTTP_RESPONSE_LIMIT:raise ValueError('Tool proxy response exceeds limit')
                    result={'request_id':request_id,'status':response.status_code,'data':base64.b64encode(data).decode()}
        except (KeyError,TypeError,ValueError,httpx.HTTPError):
            if streaming:
                with suppress(RuntimeUnavailableError):
                    if started:
                        await self.request('http_abort',request_id=request_id)
                    else:
                        await self.request('http_start',request_id=request_id,status=502,content_type='application/json')
                        await self.request('http_end',request_id=request_id)
                return
            result={'request_id':request_id,'status':502,'data':''}
        with suppress(RuntimeUnavailableError):
            await self.request('http_response',**result)

    async def close(self):
        async with self._close_lock:
            if self.closed:return
            self.closing=True
            try:
                with suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.request('close')
            finally:
                # A remote acknowledgement is not proof of process/VM termination.
                await self.transport.close()
                self.closed=True
                for task in (self.reader,self.stderr,*self.forwarding):task.cancel()
                await asyncio.gather(self.reader,self.stderr,*self.forwarding,return_exceptions=True)
