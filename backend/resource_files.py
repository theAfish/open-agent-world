"""Generic streamed resource-action uploads; file bytes never enter action JSON."""
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import PurePosixPath

from fastapi import HTTPException
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException
from python_multipart.exceptions import MultipartParseError

from backend.file_transfer import MAX_FILE_BYTES, FILE_CHUNK_BYTES, TransferredFile
from backend.node_resources import ResourceActionRequest

METADATA_BYTES = 4 * 1024 * 1024
FRAMING_BYTES = 1024 * 1024


@asynccontextmanager
async def resource_files(request):
    """Parse bounded multipart files into private seekable leases.

    The multipart parser spools to disk. Its handles live through the action's
    cancellation join and are closed together on every exit path.
    """
    async def chunks():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FILE_BYTES + METADATA_BYTES + FRAMING_BYTES:
                raise HTTPException(413, "File upload exceeds the 512 MiB transfer limit")
            yield chunk

    # Parser cleanup handles its own parse errors. Other failures (including our
    # streamed size check) must also close files already allocated by the parser.
    parser = MultiPartParser(request.headers, chunks(), max_files=1000, max_fields=1,
                             max_part_size=METADATA_BYTES)
    # A folder can contain many small files. Roll each one to disk immediately
    # instead of retaining up to 1 MiB * 1000 files in parser memory.
    parser.spool_max_size = 1
    try:
        try:
            form = await parser.parse()
        except (MultiPartException, MultipartParseError) as error:
            raise HTTPException(400, "Invalid multipart file upload") from error
        try:
            metadata = form.get("request")
            if not isinstance(metadata, str):
                raise HTTPException(422, "Supply resource-action metadata in the request field")
            try:
                action_request = ResourceActionRequest.model_validate(json.loads(metadata))
            except ValueError as error:
                raise HTTPException(422, "Invalid resource-action metadata") from error
            files = {}
            metadata_fields = 0
            total_bytes = 0
            for key, upload in form.multi_items():
                if key == "request":
                    metadata_fields += 1
                    if metadata_fields > 1:
                        raise HTTPException(422, "Duplicate resource-action metadata")
                    continue
                if not isinstance(upload, UploadFile) or key in files:
                    raise HTTPException(422, "File fields must have unique names")
                name = upload.filename or ""
                if not name or PurePosixPath(name).name != name or "\\" in name or len(name) > 255:
                    raise HTTPException(422, "Invalid upload filename")
                size, digest = 0, hashlib.sha256()
                while chunk := await upload.read(FILE_CHUNK_BYTES):
                    size += len(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > MAX_FILE_BYTES:
                        raise HTTPException(413, "File upload exceeds the 512 MiB transfer limit")
                    digest.update(chunk)
                await upload.seek(0)
                files[key] = TransferredFile(upload.file, name, size, digest.hexdigest(),
                                             upload.content_type or "application/octet-stream")
            if not files:
                raise HTTPException(422, "Supply at least one file")
            yield action_request, files
        finally:
            await form.close()
    finally:
        for file in parser._files_to_close_on_error:
            file.close()
