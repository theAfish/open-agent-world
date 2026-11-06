"""Portable SDK-boundary checks; the real relay receiver is tested separately."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.agents.execution import RuntimeProcess
from backend.cloud.aliyun.execution import AliyunRuntimeExecution
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.sandbox.transfers import CHUNK_BYTES


class ControlProcess(RuntimeProcess):
    def __init__(self, slot_overrides=None):
        self.handle = SimpleNamespace(pid=4321)
        self.slot_overrides = slot_overrides or {}
        self.frames, self.leases = [], {}
        self.replies = asyncio.Queue()
        self.stopped = asyncio.Event()
        self.close_calls = 0

    async def write(self, data):
        message = json.loads(data)
        self.frames.append(message)
        method, options = message['method'], message['params']
        if method == 'prepare_file_chunk':
            result = {'chunk_id': uuid4().hex, 'descriptor': 17, 'pid': self.handle.pid, **self.slot_overrides}
            self.leases[result['chunk_id']] = result
        elif method == 'upload_file':
            operation = options['operation']
            if operation == 'upload_begin':
                result = {'staging': '.oaw-upload-fixture.part', 'signature': [1, 2, 0, 3, 4]}
            elif operation == 'upload_chunk':
                self.leases.pop(options['chunk_id'])
                result = {'signature': [1, 2, options['offset'], 3, 4]}
            elif operation == 'upload_commit':
                result = {'path': '/workspace/' + options['path']}
            else:
                result = {'cancelled': True}
        elif method == 'close':
            result = {}
        else:
            raise AssertionError('Unexpected control operation: ' + method)
        await self.replies.put(json.dumps({'id': message['id'], 'result': result}).encode() + b'\n')

    async def readline(self):
        return await self.replies.get()

    async def read(self, size=16384):
        return await self.readline()

    async def read_stderr(self, size=8192):
        await self.stopped.wait()
        return b''

    async def wait(self):
        await self.stopped.wait()
        return 0

    async def close(self):
        self.close_calls += 1
        self.leases.clear()  # This boundary represents confirmed owned VM destruction.
        self.stopped.set()
        await self.replies.put(b'')


@asynccontextmanager
async def channel(*, slot_overrides=None, sdk_write=None):
    transport, writes = ControlProcess(slot_overrides), []

    async def write(path, data, **options):
        writes.append((path, data, options))
        if sdk_write is not None:
            await sdk_write()

    execution = AliyunRuntimeExecution(transport, SimpleNamespace(files=SimpleNamespace(write=write)))
    try:
        yield execution, transport, writes
    finally:
        await execution.close()


@pytest.mark.asyncio
async def test_binary_chunks_use_owned_fd_and_keep_file_bytes_out_of_control_frames():
    content = (bytes(range(256)) * 1024, b'tail')

    async def source():
        for chunk in content:
            yield chunk

    async with channel() as (execution, transport, writes):
        assert await execution.write_file_stream('input.bin', source()) == '/workspace/input.bin'
        assert [item[1] for item in writes] == list(content)
        assert all(path == '/proc/4321/fd/17' and options == {'user': 'user', 'request_timeout': 20}
                   for path, _, options in writes)
        prepared = [frame['params'] for frame in transport.frames if frame['method'] == 'prepare_file_chunk']
        assert prepared == [{'size': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()} for chunk in content]
        assert all('data' not in frame['params'] for frame in transport.frames)
        assert not transport.leases
    assert execution.closed and transport.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('slot_overrides', [
    {'pid': 9999}, {'pid': True}, {'descriptor': True}, {'descriptor': 2},
    {'descriptor': 1048577}, {'chunk_id': '../untrusted'},
])
async def test_invalid_remote_slot_cannot_select_an_sdk_write_target(slot_overrides):
    async with channel(slot_overrides=slot_overrides) as (execution, transport, writes):
        with pytest.raises(RuntimeUnavailableError, match='outcome is uncertain'):
            await execution._file_upload('upload_chunk', 'input.bin', data='eA==')
        assert not writes
        assert [frame['method'] for frame in transport.frames] == ['prepare_file_chunk']
        assert transport.leases  # No attempt to discard a slot after an invalid response.
    assert execution.closed and transport.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type', [TimeoutError, asyncio.CancelledError])
async def test_uncertain_sdk_outcome_retains_slot_blocks_queued_upload_and_closes_vm(error_type):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fail_write():
        entered.set()
        await release.wait()
        raise error_type()

    async with channel(sdk_write=fail_write) as (execution, transport, writes):
        first = asyncio.create_task(execution._file_upload('upload_chunk', 'first.bin', data='eA=='))
        await asyncio.wait_for(entered.wait(), 2)
        queued = asyncio.create_task(execution._file_upload('upload_chunk', 'second.bin', data='eQ=='))
        await asyncio.sleep(0)
        assert not queued.done()
        release.set()
        first_error, queued_error = await asyncio.gather(first, queued, return_exceptions=True)
        assert isinstance(first_error, asyncio.CancelledError if error_type is asyncio.CancelledError else RuntimeUnavailableError)
        assert isinstance(execution.failure, RuntimeUnavailableError)
        assert queued_error is execution.failure
        assert len(writes) == 1 and transport.leases
        assert [frame['method'] for frame in transport.frames] == ['prepare_file_chunk']
    assert execution.closed and transport.close_calls == 1 and not transport.leases


@pytest.mark.asyncio
async def test_binary_channel_rejects_oversized_chunk_before_allocating_remote_slot():
    async with channel() as (execution, transport, writes):
        with pytest.raises(ResourceValidationError, match='chunk exceeds'):
            await execution._file_upload('upload_chunk', 'input.bin',
                                         data=base64.b64encode(b'x' * (CHUNK_BYTES + 1)).decode())
        assert not transport.frames and not writes
