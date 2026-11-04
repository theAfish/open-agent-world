"""Provider-neutral CLI sessions in owned Agent Sandbox VMs.

This is separate from the short-command Sandbox Card: a CLI and its tool relay
share one bounded VM lifetime. Only the profile's dedicated Volume persists.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import shlex

from backend.agents.execution import RuntimeExecutionProvider, RuntimeProcess
from backend.agents.execution_transport import FRAME_LIMIT, RelayRuntimeExecution, execution_worker_argv
from backend.errors import RuntimeUnavailableError
from .sandbox import AliyunSandboxBackend

OUTPUT_QUOTA = 128*1024*1024


class _Channel:
    def __init__(self):
        self.reader = asyncio.StreamReader(limit=FRAME_LIMIT)
        self.pending = 0

    def feed(self, data):
        self.pending += len(data)
        if self.pending > FRAME_LIMIT:
            raise RuntimeUnavailableError('Runtime transport consumer buffer exceeded its limit')
        self.reader.feed_data(data)

    async def read(self, size=16384, *, line=False):
        data = await self.reader.readline() if line else await self.reader.read(size)
        self.pending -= len(data)
        return data


class SandboxProcess(RuntimeProcess):
    """SDK handles and PIDs stay inside this provider; VM kill owns cleanup."""
    def __init__(self, backend, value):
        self.backend,self.value = backend,value
        self.stdout,self.stderr = _Channel(),_Channel()
        self.handle,self.monitor = None,None
        self.closed = False
        self.output_bytes = 0
        self.write_lock,self.close_lock = asyncio.Lock(),asyncio.Lock()

    def output(self, channel, text):
        data = text.encode('utf-8')
        self.output_bytes += len(data)
        if self.output_bytes > OUTPUT_QUOTA:
            raise RuntimeUnavailableError('Runtime session transfer quota exceeded')
        channel.feed(data)

    async def start(self, sandbox, operation_id):
        self.value.update(state='running',command={'id':operation_id,'state':'admitted'})
        self.backend._write(self.value)
        self.handle = await sandbox.commands.run(shlex.join(execution_worker_argv()),
            background=True,stdin=True,user='user',cwd='/',timeout=self.backend.settings.lifetime_seconds-5,
            on_stdout=lambda text:self.output(self.stdout,text),on_stderr=lambda text:self.output(self.stderr,text))
        self.value['command'].update(pid=self.handle.pid,state='running')
        self.backend._write(self.value)
        self.monitor = asyncio.create_task(self._wait())
        self.monitor.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)

    async def _wait(self):
        from e2b.sandbox.commands.command_handle import CommandExitException
        try:
            try:
                result=await self.handle.wait()
                return result.exit_code
            except CommandExitException as error:
                return error.exit_code
        except Exception:
            error=RuntimeUnavailableError(f'Alibaba runtime transport failed or reached its {self.backend.settings.lifetime_seconds}s session limit; the command was not replayed')
            self.stdout.reader.set_exception(error)
            self.stderr.reader.set_exception(error)
            raise error from None
        finally:
            self.stdout.reader.feed_eof()
            self.stderr.reader.feed_eof()

    async def write(self, data):
        if self.closed or self.handle is None or self.monitor is not None and self.monitor.done():
            raise RuntimeUnavailableError('Alibaba runtime transport is closed')
        async with self.write_lock:
            # Avoid a large unary SDK request for binary/base64 file frames.
            # A failed chunk is never submitted again because its outcome is unknown.
            for offset in range(0,len(data),64*1024):
                await self.handle.send_stdin(data[offset:offset+64*1024],request_timeout=20)

    async def readline(self):
        return await self.stdout.read(line=True)

    async def read(self,size=16384):
        return await self.stdout.read(size)

    async def read_stderr(self,size=8192):
        return await self.stderr.read(size)

    async def wait(self):
        return await asyncio.shield(self.monitor)

    async def close(self):
        async with self.close_lock:
            if self.closed:
                return
            # Do not release ownership on a mere relay acknowledgement or PID exit.
            await self.backend._join_dispose(self.value)
            self.closed=True
            if self.handle is not None:
                try:
                    async with asyncio.timeout(5):
                        await self.handle.disconnect()
                except Exception:
                    pass  # The VM is already confirmed gone.
            if self.monitor is not None:
                self.monitor.cancel()
                await asyncio.gather(self.monitor,return_exceptions=True)
            self.stdout.reader.feed_eof()
            self.stderr.reader.feed_eof()


class AliyunRuntimeExecutionProvider(RuntimeExecutionProvider):
    def __init__(self,root:Path,settings,*,sdk=None,lifetime_seconds=900):
        self.root=Path(root)
        self.settings=replace(settings,lifetime_seconds=lifetime_seconds)
        self.sdk=sdk

    def configuration(self,runtime_id):
        ready=self.settings.workspace_store=='volume' and not self.settings.unavailable_reason
        return {'mode':'remote','configured':ready,'provider':'aliyun.agent-sandbox',
                'max_execution_seconds':self.settings.lifetime_seconds}

    async def open(self,request):
        if not self.configuration(request.runtime_id)['configured']:
            raise RuntimeUnavailableError('CLI execution requires a verified dedicated persistent Volume')
        key=hashlib.sha256(request.node_id.encode()).hexdigest()
        backend=AliyunSandboxBackend(self.root/key,self.settings,sdk=self.sdk)
        logical_id='runtime-'+key[:48]
        if not backend.path.exists():
            await backend.create(logical_id)
        value=backend._read(logical_id)
        # A worker restart never resumes/replays an unfinished CLI. Reconcile and
        # confirm teardown of the recorded owner before starting a new operation.
        if value['state']=='creating':
            await backend._reconcile(value)
        if value['remote_id']:
            await backend._connect(value)  # Enforces profile/request ownership.
            await backend._join_dispose(value)
        backend._apply_network_policy(value,{'network_enabled':True})
        transport=SandboxProcess(backend,value)
        try:
            sandbox=await backend._ensure_remote(value)
            await transport.start(sandbox,request.operation_id)
            execution=RelayRuntimeExecution(transport)
            base='/workspace/oaw-runtimes/'+key
            try:
                return await execution.initialize(base+'/project',base+'/home',environment={
                    'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':base+'/home','USER':'user','LANG':'C.UTF-8',
                    'TMPDIR':'/tmp'},variables=dict(request.variables))
            except BaseException:
                await execution.close()
                raise
        except BaseException:
            # A lost process-start response may still have launched code. Kill
            # the owned VM, retain an uncertain receipt if kill cannot be confirmed.
            if value['state']=='creating':
                await backend._reconcile(value)
            await transport.close()
            raise
