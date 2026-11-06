"""Shared file-data policy, independent of control messages and parser budgets.

File contents travel in bounded chunks. Synchronous consumers receive a private,
seekable temporary handle only after the complete transfer has been verified.
The handle is never a model argument or a user-selected host path.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
import hashlib
import tempfile
from typing import BinaryIO

from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.sandbox.file_policy import (
    MAX_FILE_BYTES, FILE_CHUNK_BYTES, INLINE_FILE_BYTES, file_transfer_policy,
)


def validate_file_limit(max_bytes: int) -> int:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_FILE_BYTES:
        raise ResourceValidationError("File transfer limit must be between 1 byte and 512 MiB")
    return max_bytes


async def bounded_chunks(chunks: AsyncIterator[bytes], *, max_bytes: int = MAX_FILE_BYTES):
    """Normalize transport chunks and close the source on success or failure."""
    size = 0
    async with aclosing(chunks):
        validate_file_limit(max_bytes)
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ResourceValidationError("File chunks must be bytes")
            size += len(chunk)
            if size > max_bytes:
                raise ResourceValidationError(f"File exceeds the {max_bytes}-byte transfer limit")
            for offset in range(0, len(chunk), FILE_CHUNK_BYTES):
                yield chunk[offset:offset + FILE_CHUNK_BYTES]


@dataclass(frozen=True)
class TransferredFile:
    """Host-owned lease for a verified file; valid only inside its context."""

    stream: BinaryIO
    name: str
    size_bytes: int
    sha256: str
    media_type: str = "application/octet-stream"

    async def chunks(self):
        self.stream.seek(0)
        while chunk := self.stream.read(FILE_CHUNK_BYTES):
            yield chunk
            await asyncio.sleep(0)


@asynccontextmanager
async def receive_file(chunks: AsyncIterator[bytes], *, name: str,
                       media_type: str = "application/octet-stream",
                       max_bytes: int = MAX_FILE_BYTES, expected_sha256: str | None = None):
    """Spool to disk, verify EOF, then lend a seekable stream to a consumer.

    No persistent destination is published on incomplete/cancelled transfers.
    TemporaryFile gives each concurrent transfer its own non-addressable handle.
    """
    with tempfile.TemporaryFile("w+b") as stream:
        digest = hashlib.sha256()
        size = 0
        async with aclosing(bounded_chunks(chunks, max_bytes=max_bytes)) as source:
            async for chunk in source:
                # Disk writes are bounded; no full-file allocation or base64 copy.
                # Keep the write synchronous so cancellation cannot close its handle
                # while a detached thread is still using it.
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                await asyncio.sleep(0)
        checksum = digest.hexdigest()
        if expected_sha256 is not None and checksum != expected_sha256:
            raise ResourceValidationError("File checksum does not match")
        stream.seek(0)
        yield TransferredFile(stream, name, size, checksum, media_type)


async def _settle(operation):
    """Observe a side effect through cancellation before releasing its ownership."""
    task = asyncio.ensure_future(operation)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    return task.result(), interrupted



class FileTransferEndpoint:
    """Pinned, staged file protocol shared by runtime and Sandbox adapters.

    Verified source EOF precedes an exclusive destination copy. Existing files
    are preserved; this does not promise atomic visibility to concurrent commands.
    """

    async def read_file_stream(self, path: str, *, root: str = 'workspace',
                               max_bytes: int = MAX_FILE_BYTES) -> AsyncIterator[bytes]:
        """Read scoped chunks while pinning one file identity through verified EOF.

        Consumers must exhaust the iterator to establish complete file integrity.
        File descriptors close before each bounded chunk is yielded.
        """
        validate_file_limit(max_bytes)
        info = await self._file_download('download_info', path, root=root)
        if not isinstance(info, dict):
            raise ResourceValidationError('Runtime file has an invalid identity')
        size, signature = info.get('size'), info.get('signature')
        if (type(size) is not int or not 0 <= size <= max_bytes
                or not isinstance(signature, list) or len(signature) != 5
                or any(type(value) is not int for value in signature)):
            raise ResourceValidationError('Runtime file exceeds its stream limit or has an invalid identity')
        offset = 0
        while True:
            result = await self._file_download('download_chunk', path, root=root,
                                               expected=signature, offset=offset)
            if not isinstance(result, dict):
                raise ResourceValidationError('Runtime file chunk has an invalid response')
            encoded = result.get('data')
            if not isinstance(encoded, str) or len(encoded) > 4 * ((FILE_CHUNK_BYTES + 2) // 3):
                raise ResourceValidationError('Runtime file chunk exceeds its limit')
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ResourceValidationError('Runtime file chunk is not valid base64') from error
            if (type(result.get('size')) is not int or result['size'] != len(data)
                    or len(data) > FILE_CHUNK_BYTES or offset + len(data) > size):
                raise ResourceValidationError('Runtime file chunk differs from its declared size')
            if offset == size:
                # This extra read rechecks the pinned identity after the last yield.
                return
            if not data:
                raise ResourceValidationError('Runtime file ended before its declared size')
            yield data
            offset += len(data)

    async def write_file_stream(self, path: str, chunks: AsyncIterator[bytes], *,
                              root: str = 'workspace', max_bytes: int = MAX_FILE_BYTES) -> str:
        """Stage bounded chunks, committing a new file only after verified EOF.

        Uses the Sandbox uploader's no-follow, exclusive destination and cleanup
        contract. Existing files are never overwritten; close the source on error.
        """
        state = None
        committed = False
        offset = 0
        failure = None
        try:
            async with aclosing(bounded_chunks(chunks, max_bytes=max_bytes)) as source:
                # Start the source owner before admission so even a failed begin
                # closes an input stream that already owns an open file.
                chunk = await anext(source, None)
                state, interrupted = await _settle(self._file_upload('upload_begin', path, root=root))
                if interrupted:
                    raise asyncio.CancelledError
                while chunk is not None:
                    result, interrupted = await _settle(self._file_upload('upload_chunk', path, root=root,
                        staging=state['staging'], expected=state['signature'], offset=offset,
                        data=base64.b64encode(chunk).decode('ascii')))
                    state['signature'] = result['signature']
                    offset += len(chunk)
                    if interrupted:
                        raise asyncio.CancelledError
                    chunk = await anext(source, None)
            result, interrupted = await _settle(self._file_upload('upload_commit', path, root=root,
                staging=state['staging'], expected=state['signature'], offset=offset))
            committed = True
            if interrupted:
                raise asyncio.CancelledError
            return result['path']
        except BaseException as error:
            failure = error
            raise
        finally:
            if state is not None and not committed:
                try:
                    await _settle(self._file_upload('upload_abort', path, root=root,
                        staging=state['staging'], expected=state['signature']))
                except BaseException as error:
                    if failure is None:
                        raise
                    failure.add_note(f'File staging cleanup failed ({type(error).__name__})')

    async def _file_upload(self, operation, path, *, root='workspace', **options):
        raise RuntimeUnavailableError('This execution provider does not support streamed file transfers')

    async def _file_download(self, operation, path, *, root='workspace', **options):
        raise RuntimeUnavailableError('This execution provider does not support streamed file reads')
