"""Graph-authorized adapter for the shared pinned file-transfer protocol."""
from backend.file_transfer import FileTransferEndpoint


class SandboxFileTransfer(FileTransferEndpoint):
    def __init__(self, services, sandbox_id, authorize):
        self.services, self.sandbox_id, self.authorize = services, sandbox_id, authorize
        self.result = None
        self.read_info = None

    async def _operation(self, operation, path, *, root='workspace', **options):
        # Abort addresses only our owned staging receipt. Revoked admission must
        # still be able to remove that staging file without granting a new write.
        async with self.services._node_mutation(read_only=operation.startswith('download')):
            if operation != 'upload_abort':
                self.authorize()
            result = await self.services._require_sandbox_backend().file_operation(
                self.sandbox_id, operation, root=root, path=path, **options)
            if operation == 'upload_commit':
                self.result = {**result, 'path': path}
            elif operation == 'download_info':
                self.read_info = result
            return {**result, 'path': path}

    _file_upload = _operation
    _file_download = _operation
