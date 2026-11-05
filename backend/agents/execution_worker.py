"""Standard-library relay run inside a provider-owned execution environment.

The hosting provider supplies the OS boundary. This relay reuses the same native
process and no-follow file contracts, and forwards only registered tool endpoints.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import json
from pathlib import Path
import sys
from uuid import uuid4
from urllib.parse import urlsplit

from backend.agents.execution import FILE_LIMIT, NativeRuntimeExecutionProvider, RuntimeExecutionRequest

FRAME_LIMIT = 48 * 1024 * 1024
HTTP_REQUEST_LIMIT = 1024 * 1024
HTTP_RESPONSE_LIMIT = 8 * 1024 * 1024


class ExecutionRelay:
    def __init__(self):
        self.execution = None
        self.processes = {}
        self.pumps = set()
        self.servers = []
        self.http_pending = {}
        self.handlers = set()

    def send(self, value):
        line = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        if len(line) > FRAME_LIMIT:
            raise ValueError('Execution relay frame exceeds limit')
        sys.stdout.write(line + '\n')
        sys.stdout.flush()

    def task(self, operation, target):
        task = asyncio.create_task(operation)
        target.add(task)
        task.add_done_callback(target.discard)
        return task

    async def pump(self, process_id, process):
        async def forward(kind, read):
            try:
                while data := await read():
                    self.send({'event':kind, 'process_id':process_id,
                        'data':base64.b64encode(data).decode()})
            finally:
                self.send({'event':kind, 'process_id':process_id, 'data':None})
        # Read fixed chunks, not application lines: ACP messages may be large.
        out = asyncio.create_task(forward('stdout', lambda: process.read(16384)))
        err = asyncio.create_task(forward('stderr', lambda: process.read_stderr(16384)))
        try:
            code = await process.wait()
            await asyncio.gather(out, err)
            self.send({'event':'exit','process_id':process_id,'returncode':code})
        finally:
            for task in (out, err):
                task.cancel()
            await asyncio.gather(out, err, return_exceptions=True)

    async def http(self, proxy_id, expected_path, reader, writer, *, path_prefix=False, stream=False):
        request_id = uuid4().hex
        responded = False
        try:
            async with asyncio.timeout(10):
                header = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
                first, *lines = header.rstrip('\r\n').split('\r\n')
                method, raw_target, version = first.split(' ')
                target = urlsplit(raw_target)
                path = target.path
                pairs = [line.split(':',1) for line in lines]
                headers = {k.lower():v.strip() for k,v in pairs}
                permitted_path=path==expected_path or path_prefix and path.startswith(expected_path.rstrip('/')+'/')
                if (method != 'POST' or target.scheme or target.netloc or target.fragment
                        or (target.query and not path_prefix) or not permitted_path or version != 'HTTP/1.1'
                        or len(headers) != len(pairs) or 'transfer-encoding' in headers):
                    raise ValueError('Unsupported tool proxy request')
                length = int(headers.get('content-length','-1'))
                if not 0 <= length <= (FILE_LIMIT if stream else HTTP_REQUEST_LIMIT):
                    raise ValueError('Tool proxy request exceeds limit')
                body = await reader.readexactly(length)
            if len(self.http_pending) >= 16:
                raise ValueError('Too many tool proxy requests')
            future = asyncio.Queue(maxsize=4) if stream else asyncio.get_running_loop().create_future()
            self.http_pending[request_id] = future
            self.send({'event':'http','proxy_id':proxy_id,'request_id':request_id,
                'path':path,'query':target.query,'headers':headers,'data':base64.b64encode(body).decode()})
            if stream:
                kind,reply=await asyncio.wait_for(future.get(),600)
                if kind!='start':raise ValueError('Missing HTTP stream headers')
                status=reply['status'];media=reply['content_type']
                if (type(status) is not int or not 200<=status<=599 or not isinstance(media,str)
                        or len(media)>200 or any(ord(c)<32 or ord(c)>126 for c in media)):
                    raise ValueError('Invalid HTTP stream headers')
                writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: {media}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n'.encode())
                responded = True
                await writer.drain()
                size=0
                while True:
                    kind,reply=await asyncio.wait_for(future.get(),600)
                    if kind=='end':
                        writer.write(b'0\r\n\r\n');await writer.drain();return
                    if kind!='chunk':raise ValueError('HTTP stream was interrupted')
                    data=base64.b64decode(reply['data'],validate=True);size+=len(data)
                    if not data or len(data)>16384 or size>HTTP_RESPONSE_LIMIT:
                        raise ValueError('Invalid HTTP stream data')
                    writer.write(f'{len(data):x}\r\n'.encode()+data+b'\r\n')
                    await writer.drain()
            reply = await asyncio.wait_for(future,600)
            data = base64.b64decode(reply['data'],validate=True)
            status = reply['status']
            if type(status) is not int or not 100 <= status <= 599 or len(data) > HTTP_RESPONSE_LIMIT:
                raise ValueError('Invalid tool proxy response')
            # Transport headers and redirects never become authority on either side.
            writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n'.encode()+data)
            await writer.drain()
        except (ValueError,OSError,TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            if not responded:
                with suppress(OSError):
                    writer.write(b'HTTP/1.1 502 Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                    await writer.drain()
        finally:
            self.http_pending.pop(request_id,None)
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def dispatch(self, method, args):
        if method == 'initialize':
            if self.execution is not None:
                raise ValueError('Execution relay is already initialized')
            request = RuntimeExecutionRequest('relay','relay',Path(args['workspace']),Path(args['home']),args.get('environment',{}),args.get('variables',{}))
            self.execution = await NativeRuntimeExecutionProvider().open(request)
            return {'workspace':self.execution.workspace,'home':self.execution.home,'environment':self.execution.environment}
        if self.execution is None:
            raise ValueError('Execution relay is not initialized')
        if method == 'spawn':
            process_id = args['process_id']
            if not isinstance(process_id,str) or len(process_id)>100 or process_id in self.processes:
                raise ValueError('Invalid or duplicate process identity')
            process = await self.execution.spawn(args['argv'],env=args['env'])
            self.processes[process_id] = process
            self.task(self.pump(process_id,process),self.pumps)
            return {}
        if method in {'stdin','close_process'}:
            process = self.processes[args['process_id']]
            if method == 'stdin':
                await process.write(base64.b64decode(args['data'],validate=True))
            else:
                await process.close()
            return {}
        if method == 'read_file':
            data = await self.execution.read_file(args['path'],root=args['root'],max_bytes=args['max_bytes'])
            return {'data':base64.b64encode(data).decode()}
        if method == 'write_file':
            path = await self.execution.write_file(args['path'],base64.b64decode(args['data'],validate=True),root=args['root'])
            return {'path':path}
        if method == 'forward_http':
            proxy_id,path = args['proxy_id'],args['path']
            if not isinstance(path,str) or not path.startswith('/') or '\r' in path or '\n' in path:
                raise ValueError('Invalid tool proxy path')
            prefix,stream=args.get('path_prefix',False),args.get('stream',False)
            if type(prefix) is not bool or type(stream) is not bool:
                raise ValueError('Invalid HTTP forwarding options')
            server = await asyncio.start_server(lambda r,w:self.task(self.http(proxy_id,path,r,w,path_prefix=prefix,stream=stream),self.handlers),'127.0.0.1',0,limit=16384)
            self.servers.append(server)
            return {'url':f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}{path}'}
        if method == 'http_response':
            future = self.http_pending.get(args['request_id'])
            if future is not None and not future.done():
                future.set_result(args)
            return {}
        if method in {'http_start','http_chunk','http_end','http_abort'}:
            queue=self.http_pending.get(args['request_id'])
            if not isinstance(queue,asyncio.Queue):return {'accepted':False}
            await asyncio.wait_for(queue.put((method[5:],args)),30)
            return {'accepted':True}
        if method == 'close':
            await self.execution.close()
            return {}
        raise ValueError('Unknown execution relay operation')

    async def request(self, value):
        request_id = value.get('id')
        try:
            result = await self.dispatch(value['method'],value.get('params',{}))
            self.send({'id':request_id,'result':result})
        except Exception as error:
            self.send({'id':request_id,'error':{'type':type(error).__name__,'message':str(error)}})

    async def serve(self):
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.buffer.readline,FRAME_LIMIT+1)
                if not line:
                    break
                if len(line)>FRAME_LIMIT:
                    raise ValueError('Execution relay frame exceeds limit')
                value = json.loads(line)
                if not isinstance(value,dict) or len(self.handlers)>=128:
                    raise ValueError('Execution relay request limit exceeded')
                self.task(self.request(value),self.handlers)
        finally:
            for server in self.servers:
                server.close()
                await server.wait_closed()
            for task in tuple(self.handlers):
                task.cancel()
            await asyncio.gather(*self.handlers,return_exceptions=True)
            if self.execution is not None:
                await self.execution.close()
            await asyncio.gather(*self.pumps,return_exceptions=True)


def main():
    asyncio.run(ExecutionRelay().serve())
