"""File-data limits shared by the host and captured standalone file workers.

Keep this module standard-library-only and independent of the host package:
WSL workers load the same source in their private namespace.
"""

MAX_FILE_BYTES = 512 * 1024 * 1024
FILE_CHUNK_BYTES = 256 * 1024
INLINE_FILE_BYTES = 32 * 1024 * 1024


def file_transfer_policy():
    return {"max_file_bytes": MAX_FILE_BYTES, "chunk_bytes": FILE_CHUNK_BYTES}
