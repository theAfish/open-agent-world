"""Standalone HTTP adapter for the same binary file leases used by the OAW card.

This app also runs without OAW installed. Hosted card requests use OAW's parser;
only this separate service owns these bounded Starlette multipart handles.
"""
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import PurePosixPath
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from python_multipart.exceptions import MultipartParseError

from ..file_io import FILE_CHUNK_BYTES, INLINE_FILE_BYTES, MAX_FILE_BYTES

METADATA_BYTES = 4 * 1024 * 1024
FRAMING_BYTES = 1024 * 1024


@asynccontextmanager
async def action_input(request):
    multipart = request.headers.get('content-type', '').startswith('multipart/form-data')
    # Keep the legacy inline JSON API bounded independently of binary uploads.
    limit = (MAX_FILE_BYTES + METADATA_BYTES + FRAMING_BYTES if multipart else
             4 * ((INLINE_FILE_BYTES + 2) // 3) + METADATA_BYTES)

    async def chunks():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, 'Upload exceeds the file transfer limit')
            yield chunk

    if not multipart:
        body = bytearray()
        async for chunk in chunks():
            body.extend(chunk)
        try:
            arguments = json.loads(body) if body else {}
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ValueError
        except ValueError as error:
            raise HTTPException(422, 'Supply an arguments object') from error
        yield arguments, False, {}
        return

    parser = MultiPartParser(request.headers, chunks(), max_files=1000, max_fields=1,
                             max_part_size=METADATA_BYTES)
    parser.spool_max_size = 1
    try:
        try:
            form = await parser.parse()
        except (MultiPartException, MultipartParseError) as error:
            raise HTTPException(400, 'Invalid multipart file upload') from error
        try:
            metadata = form.get('request')
            try:
                value = json.loads(metadata) if isinstance(metadata, str) else None
                if (not isinstance(value, dict) or set(value) - {'arguments', 'confirm'} or
                        not isinstance(value.get('arguments', {}), dict) or
                        not isinstance(value.get('confirm', False), bool)):
                    raise ValueError
            except ValueError as error:
                raise HTTPException(422, 'Invalid action metadata in request field') from error
            files, names, total = {}, set(), 0
            for key, upload in form.multi_items():
                if key in names:
                    raise HTTPException(422, 'Upload fields must have unique names')
                names.add(key)
                if key == 'request':
                    continue
                if not isinstance(upload, UploadFile):
                    raise HTTPException(422, 'Supply binary file fields')
                name = upload.filename or ''
                if not name or PurePosixPath(name).name != name or '\\' in name or len(name) > 255:
                    raise HTTPException(422, 'Invalid upload filename')
                size, digest = 0, hashlib.sha256()
                while chunk := await upload.read(FILE_CHUNK_BYTES):
                    size += len(chunk)
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise HTTPException(413, 'Upload exceeds the file transfer limit')
                    digest.update(chunk)
                await upload.seek(0)
                files[key] = SimpleNamespace(stream=upload.file, name=name, size_bytes=size,
                                             sha256=digest.hexdigest(), media_type=upload.content_type)
            if not files:
                raise HTTPException(422, 'Supply at least one file')
            yield value.get('arguments', {}), value.get('confirm', False), files
        finally:
            await form.close()
    finally:
        for file in parser._files_to_close_on_error:
            file.close()
