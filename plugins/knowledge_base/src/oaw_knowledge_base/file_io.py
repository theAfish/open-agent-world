"""Borrowed file leases and host-injected policy; no dependency on an OAW host."""
import base64
import binascii
import hashlib
import io
from types import SimpleNamespace

from .errors import KnowledgeError

# Only standalone front doors own defaults. Hosted actions take the authoritative
# policy from their context; retained-source parsers do not impose a transfer cap.
STANDALONE_FILE_POLICY = {'max_file_bytes': 512 * 1024 * 1024, 'chunk_bytes': 256 * 1024}
MAX_FILE_BYTES = STANDALONE_FILE_POLICY['max_file_bytes']
FILE_CHUNK_BYTES = STANDALONE_FILE_POLICY['chunk_bytes']
INLINE_FILE_BYTES = 32 * 1024 * 1024  # Legacy JSON/model budget, separate from binary transfer.


def file_limit(context):
    return (getattr(context, 'file_transfer_policy', None) or STANDALONE_FILE_POLICY)['max_file_bytes']


def file_chunk_bytes(context):
    return (getattr(context, 'file_transfer_policy', None) or STANDALONE_FILE_POLICY)['chunk_bytes']


def input_file(context, filename, encoded=None, *, key='file'):
    file = (getattr(context, 'files', None) or {}).get(key)
    if file is None:
        if not isinstance(encoded, str) or not encoded:
            raise KnowledgeError('Provide an uploaded file')
        if len(encoded) > 4 * ((INLINE_FILE_BYTES + 2) // 3):
            raise KnowledgeError('Inline uploads are limited to 32 MiB; upload the file as binary data')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise KnowledgeError('content_base64 is not valid base64') from None
        if len(data) > INLINE_FILE_BYTES:
            raise KnowledgeError('Inline uploads are limited to 32 MiB')
        file = SimpleNamespace(stream=io.BytesIO(data), name=filename, size_bytes=len(data),
                               sha256=hashlib.sha256(data).hexdigest())
    if not 0 < file.size_bytes <= file_limit(context):
        raise KnowledgeError('The uploaded file is empty or exceeds the file transfer limit')
    file.stream.seek(0)
    return file
