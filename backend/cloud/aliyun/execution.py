"""Provider-neutral CLI sessions in owned Agent Sandbox VMs.

This is separate from the short-command Sandbox Card: a CLI and its tool relay
share one bounded VM lifetime. Only the profile's dedicated Volume persists.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
from pathlib import Path
import shlex
import time

from backend.agents.execution import RuntimeExecutionPolicy, RuntimeExecutionProvider, RuntimeProcess
from backend.agents.execution_transport import FRAME_LIMIT, RelayRuntimeExecution, execution_worker_argv
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.sandbox.transfers import CHUNK_BYTES
from .sandbox import AliyunSandboxBackend

OUTPUT_QUOTA = 128*1024*1024
# Match OAW's default one-hour run budget; the early POC used 15 minutes.
DEFAULT_RUNTIME_LIFETIME = 3600


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
        self.started_at = None
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
        self.started_at = time.monotonic()
        self.handle = await sandbox.commands.run('exec '+shlex.join(execution_worker_argv()),
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
        except Exception as cause:
            elapsed = time.monotonic() - self.started_at
            lifetime = self.backend.settings.lifetime_seconds
            if elapsed >= lifetime - 5:
                message = f'Alibaba runtime reached its configured {lifetime}s session limit'
            else:
                # SDK messages can contain authenticated URLs or remote output.
                # Keep the useful error category without exposing their contents.
                message = f'Alibaba runtime transport failed after {elapsed:.0f}s ({type(cause).__name__})'
            error=RuntimeUnavailableError(message + '; the command was not replayed')
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


class AliyunRuntimeExecution(RelayRuntimeExecution):
    """Binary file chunks use the SDK; stdin carries only control messages."""

    def __init__(self, transport, sandbox):
        super().__init__(transport)
        self.sandbox = sandbox
        self._chunk_lock = asyncio.Lock()

    def _file_chunk_target(self, slot, *, download=False):
        fields = {'chunk_id', 'descriptor', 'pid'} | ({'size', 'sha256'} if download else set())
        if (not isinstance(slot, dict) or set(slot) != fields
                or type(slot['descriptor']) is not int or not 3 <= slot['descriptor'] <= 1048576
                or type(slot['pid']) is not int or slot['pid'] != self.transport.handle.pid
                or not isinstance(slot['chunk_id'], str) or len(slot['chunk_id']) != 32
                or any(char not in '0123456789abcdef' for char in slot['chunk_id'])):
            raise RuntimeUnavailableError('Invalid runtime file chunk identity')
        if download and (type(slot['size']) is not int or not 0 <= slot['size'] <= CHUNK_BYTES
                or not isinstance(slot['sha256'], str) or len(slot['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in slot['sha256'])):
            raise RuntimeUnavailableError('Invalid runtime file chunk size or SHA256')
        return f"/proc/{slot['pid']}/fd/{slot['descriptor']}"

    async def _file_upload(self, operation, path, *, root='workspace', **options):
        if operation != 'upload_chunk':
            return await super()._file_upload(operation, path, root=root, **options)
        data = base64.b64decode(options.pop('data'), validate=True)
        if len(data) > CHUNK_BYTES:
            raise ResourceValidationError('Runtime file chunk exceeds its limit')
        if not data:
            return await super()._file_upload(operation, path, root=root, data='', **options)
        async with self._chunk_lock:
            if self.failure is not None:
                raise self.failure
            slot = await self.request('prepare_file_chunk', size=len(data), sha256=hashlib.sha256(data).hexdigest())
            try:
                # Only an anonymous FD held by this exact owned relay can be written.
                # A pathname in the mutable workspace is never passed to the SDK.
                await self.sandbox.files.write(self._file_chunk_target(slot),
                    data, user='user', request_timeout=20)
            except (Exception, asyncio.CancelledError) as error:
                # A late SDK write must never reach a reused descriptor. Retain the
                # uncertain slot and admit no more work until owned VM teardown.
                self._fail(RuntimeUnavailableError(
                    'Alibaba file transfer outcome is uncertain; the VM must be closed before further work'))
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise self.failure from error
            return await self.request('upload_file', operation=operation, path=path, root=root,
                chunk_id=slot['chunk_id'], **options)

    async def _file_download(self, operation, path, *, root='workspace', **options):
        if operation != 'download_chunk':
            return await super()._file_download(operation, path, root=root, **options)
        async with self._chunk_lock:
            if self.failure is not None:
                raise self.failure
            slot = await self.request('prepare_file_download', path=path, root=root, **options)
            try:
                target = self._file_chunk_target(slot, download=True)
                data = bytearray()
                if slot['size']:
                    # Stream even this bounded snapshot so an in-VM mutation
                    # cannot make the SDK allocate an arbitrarily enlarged FD.
                    async with asyncio.timeout(20):
                        async with await self.sandbox.files.read(target, format='stream', user='user', request_timeout=20) as stream:
                            async for chunk in stream:
                                if not isinstance(chunk, bytes) or len(data) + len(chunk) > slot['size']:
                                    raise ResourceValidationError('Runtime download chunk exceeds its declared size')
                                data.extend(chunk)
                if len(data) != slot['size'] or hashlib.sha256(data).hexdigest() != slot['sha256']:
                    raise ResourceValidationError('Runtime download chunk size or SHA256 did not match')
                # The SDK has finished and its HTTP stream is closed before the
                # relay may release/reuse this anonymous descriptor.
                await self.request('discard_file_chunk', chunk_id=slot['chunk_id'])
            except (Exception, asyncio.CancelledError) as error:
                self._fail(RuntimeUnavailableError(
                    'Alibaba file transfer outcome is uncertain; the VM must be closed before further work'))
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise self.failure from error
            # The common file protocol consumes one bounded chunk. These bytes
            # never travel over the relay's stdout/control channel.
            return {'data': base64.b64encode(data).decode('ascii'), 'size': len(data)}


class AliyunRuntimeExecutionProvider(RuntimeExecutionProvider):
    def __init__(self,root:Path,settings,*,sdk=None,lifetime_seconds=DEFAULT_RUNTIME_LIFETIME,default_permission='workspace-write'):
        self.root=Path(root)
        self.settings=replace(settings,lifetime_seconds=lifetime_seconds)
        self.sdk=sdk
        # Operator policy, never a graph/card setting or an inference from remote.
        self.policy=RuntimeExecutionPolicy(isolation='dedicated-vm',default_permission=default_permission)

    def configuration(self,runtime_id):
        ready=self.settings.workspace_store=='volume' and not self.settings.unavailable_reason
        return {'mode':'remote','configured':ready,'provider':'aliyun.agent-sandbox',
                'max_execution_seconds':self.settings.lifetime_seconds,
                'default_permission':self.policy.default_permission}

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
        ownership_verified=False
        try:
            sandbox=await backend._ensure_remote(value)
            # Fresh creates must satisfy the same ownership check as reconnects
            # before any command or host-enforced permission claim is admitted.
            await backend._verify_ownership(value,sandbox)
            ownership_verified=True
            await transport.start(sandbox,request.operation_id)
            execution=AliyunRuntimeExecution(transport,sandbox)
            base='/workspace/oaw-runtimes/'+key
            try:
                await execution.initialize(base+'/project',base+'/home',environment={
                    'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':base+'/home','USER':'user','LANG':'C.UTF-8',
                    'TMPDIR':'/tmp'},variables=dict(request.variables))
                execution.policy=self.policy
                return execution
            except BaseException:
                await execution.close()
                raise
        except BaseException:
            # A lost process-start response may still have launched code. Kill
            # the owned VM, retain an uncertain receipt if kill cannot be confirmed.
            if value['state']=='creating':
                await backend._reconcile(value)
            elif value['remote_id'] and not ownership_verified:
                # An unexpected/unverifiable identity is not ours to execute or
                # kill. Retain the binding for operator reconciliation and TTL.
                value['state']='uncertain'
                backend._write(value)
                raise
            await transport.close()
            raise
