import hashlib
import os
from contextlib import asynccontextmanager

import pytest

from backend.agents.execution_worker import ExecutionRelay
from backend.sandbox.transfers import CHUNK_BYTES


@asynccontextmanager
async def receiver(tmp_path):
    relay = ExecutionRelay()
    await relay.dispatch('initialize', {'workspace': str(tmp_path / 'project'), 'home': str(tmp_path / 'home')})
    try:
        yield relay
    finally:
        await relay.dispatch('close', {})


async def prepare(relay, content, *, size=None, checksum=None):
    slot = await relay.dispatch('prepare_file_chunk', {
        'size': len(content) if size is None else size,
        'sha256': hashlib.sha256(content).hexdigest() if checksum is None else checksum,
    })
    assert slot['pid'] == os.getpid() and type(slot['descriptor']) is int
    os.write(slot['descriptor'], content)
    return slot


async def append(relay, state, slot, offset):
    return await relay.dispatch('upload_file', {'operation': 'upload_chunk', 'path': 'input.bin',
        'root': 'workspace', 'staging': state['staging'], 'expected': state['signature'],
        'offset': offset, 'chunk_id': slot['chunk_id']})


@pytest.mark.asyncio
async def test_verified_anonymous_chunks_use_existing_scoped_upload(tmp_path):
    async with receiver(tmp_path) as relay:
        state = await relay.dispatch('upload_file', {'operation': 'upload_begin', 'path': 'input.bin'})
        for offset, content in ((0, b'first'), (5, b'second')):
            slot = await prepare(relay, content)
            result = await append(relay, state, slot, offset)
            state['signature'] = result['signature']
            with pytest.raises(OSError):
                os.fstat(slot['descriptor'])
        await relay.dispatch('upload_file', {'operation': 'upload_commit', 'path': 'input.bin',
            'staging': state['staging'], 'expected': state['signature'], 'offset': 11})
        assert (tmp_path / 'project/input.bin').read_bytes() == b'firstsecond'
        assert not relay.file_chunks


@pytest.mark.asyncio
@pytest.mark.parametrize('content,size,checksum', [
    (b'bad', 3, hashlib.sha256(b'good').hexdigest()),
    (b'short', 6, None),
    (b'long', 3, None),
])
async def test_tampered_chunk_never_changes_existing_staging(tmp_path, content, size, checksum):
    async with receiver(tmp_path) as relay:
        state = await relay.dispatch('upload_file', {'operation': 'upload_begin', 'path': 'input.bin'})
        prefix = await prepare(relay, b'prefix')
        state['signature'] = (await append(relay, state, prefix, 0))['signature']
        slot = await prepare(relay, content, size=size, checksum=checksum)
        with pytest.raises(ValueError, match='did not match'):
            await append(relay, state, slot, 6)
        assert (tmp_path / 'project' / state['staging']).read_bytes() == b'prefix'
        assert not (tmp_path / 'project/input.bin').exists()
        with pytest.raises(OSError):
            os.fstat(slot['descriptor'])
        await relay.dispatch('upload_file', {'operation': 'upload_abort', 'path': 'input.bin',
            'staging': state['staging'], 'expected': state['signature']})


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [
    {'size': 0, 'sha256': '0' * 64},
    {'size': True, 'sha256': '0' * 64},
    {'size': CHUNK_BYTES + 1, 'sha256': '0' * 64},
    {'size': 1, 'sha256': 'wrong'},
    {'size': 1, 'sha256': '0' * 64, 'path': '../untrusted'},
])
async def test_invalid_chunk_metadata_allocates_nothing(tmp_path, options):
    async with receiver(tmp_path) as relay:
        with pytest.raises(ValueError, match='Invalid file chunk'):
            await relay.dispatch('prepare_file_chunk', options)
        assert not relay.file_chunks


@pytest.mark.asyncio
async def test_chunk_tokens_quota_discard_and_close(tmp_path):
    async with receiver(tmp_path) as relay:
        slots = [await prepare(relay, b'x') for _ in range(4)]
        with pytest.raises(ValueError, match='limit exceeded'):
            await prepare(relay, b'x')
        for method, options in [('discard_file_chunk', {'chunk_id': 'unknown'}),
            ('upload_file', {'operation': 'upload_chunk', 'path': 'input.bin', 'chunk_id': 'unknown'}),
            ('upload_file', {'operation': 'upload_begin', 'path': 'input.bin', 'chunk_id': slots[0]['chunk_id']}),
            ('upload_file', {'operation': 'upload_chunk', 'path': 'input.bin',
                             'chunk_id': slots[0]['chunk_id'], 'data': 'eA=='})]:
            with pytest.raises(ValueError):
                await relay.dispatch(method, options)
        await relay.dispatch('discard_file_chunk', {'chunk_id': slots[0]['chunk_id']})
        assert len(relay.file_chunks) == 3
        with pytest.raises(OSError):
            os.fstat(slots[0]['descriptor'])
        await relay.dispatch('close', {})
        assert not relay.file_chunks
        for slot in slots[1:]:
            with pytest.raises(OSError):
                os.fstat(slot['descriptor'])
        with pytest.raises(ValueError, match='relay is closed'):
            await prepare(relay, b'x')


@pytest.mark.asyncio
async def test_download_leases_snapshot_only_pinned_scoped_chunks_and_release_descriptors(tmp_path):
    from backend.errors import ResourceValidationError
    async with receiver(tmp_path) as relay:
        await relay.execution.write_file('result.bin', b'file-result')
        info = await relay.execution._file_download('download_info', 'result.bin')
        arguments = {'path': 'result.bin', 'root': 'workspace', 'expected': info['signature'], 'offset': 0}
        slot = await relay.dispatch('prepare_file_download', arguments)
        assert slot['size'] == 11 and slot['sha256'] == hashlib.sha256(b'file-result').hexdigest()
        assert slot['pid'] == os.getpid() and os.read(slot['descriptor'], CHUNK_BYTES) == b'file-result'
        assert set(slot) == {'chunk_id', 'descriptor', 'pid', 'size', 'sha256'}
        await relay.dispatch('discard_file_chunk', {'chunk_id': slot['chunk_id']})
        with pytest.raises(OSError):
            os.fstat(slot['descriptor'])
        assert not relay.file_chunks
        (tmp_path / 'project/result.bin').write_bytes(b'replaced')
        with pytest.raises(ResourceValidationError, match='changed'):
            await relay.dispatch('prepare_file_download', arguments)
        assert not relay.file_chunks


@pytest.mark.asyncio
async def test_download_leases_share_quota_and_cannot_be_repurposed_as_uploads(tmp_path):
    async with receiver(tmp_path) as relay:
        await relay.execution.write_file('result.bin', b'result')
        info = await relay.execution._file_download('download_info', 'result.bin')
        arguments = {'path': 'result.bin', 'root': 'workspace', 'expected': info['signature'], 'offset': 0}
        uploads = [await prepare(relay, b'x') for _ in range(3)]
        download = await relay.dispatch('prepare_file_download', arguments)
        with pytest.raises(ValueError, match='limit exceeded'):
            await prepare(relay, b'x')
        with pytest.raises(ValueError, match='not an upload'):
            await relay.dispatch('upload_file', {'operation': 'upload_chunk', 'path': 'input.bin',
                'root': 'workspace', 'chunk_id': download['chunk_id']})
        await relay.dispatch('close', {})
        for slot in [*uploads, download]:
            with pytest.raises(OSError):
                os.fstat(slot['descriptor'])
