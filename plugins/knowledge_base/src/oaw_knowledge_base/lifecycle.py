"""Create, validate and clean up the card's single SQL file."""
from open_agent_world.plugin_api import (
    ConflictError, NodeLifecycleHandler, NodeLifecycleTransaction,
)

from .client import FILE_NAME, close_client, database_path, open_client
from .errors import KnowledgeError


def remove_files(directory):
    # Only the plugin's known files, never recursive deletion of a user folder.
    for suffix in ("-wal", "-shm", "-journal", ""):
        (directory / (FILE_NAME + suffix)).unlink(missing_ok=True)
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


class CreateKnowledgeBase(NodeLifecycleTransaction):
    def __init__(self, node_id, directory):
        self.node_id = node_id
        self.directory = directory
        self.created = False

    async def commit(self):
        if self.created:
            return
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.mkdir()
        except FileExistsError as error:
            raise ConflictError("Knowledge storage already exists; use a new card ID") from error
        self.created = True
        # Materialize the schema now so the first action is not the first migration.
        open_client(self.node_id, self.directory)

    async def rollback(self, error):
        if self.created:
            close_client(self.node_id)
            remove_files(self.directory)
            self.created = False


class DeleteKnowledgeBase(NodeLifecycleTransaction):
    def __init__(self, node_id, directory):
        self.node_id = node_id
        self.directory = directory

    async def finalize(self):
        # Host journals this finalizer and retries it after restart if needed.
        close_client(self.node_id)
        remove_files(self.directory)


class KnowledgeLifecycle(NodeLifecycleHandler):
    async def prepare_create(self, context, node, request):
        return CreateKnowledgeBase(node.id, context.resources.node_storage_path(node.id))

    async def prepare_delete(self, context, node):
        return DeleteKnowledgeBase(node.id, context.resources.node_storage_path(node.id))

    async def on_startup(self, context, node):
        directory = context.resources.node_storage_path(node.id)
        if not database_path(directory).exists():
            # Nothing to recover yet; the first action opens the database.
            return
        try:
            open_client(node.id, directory)
        except (KnowledgeError, ConflictError, OSError):
            context.nodes.update_status(node.id, "error")
        else:
            if node.status != "available":
                context.nodes.update_status(node.id, "available")

    async def on_shutdown(self, context, node):
        close_client(node.id)
