"""Bounded file reads for connected viewers; source storage remains authoritative."""
import base64
from contextlib import aclosing
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import PermissionDeniedError, ResourceValidationError

MAX_FILE_BYTES = 16 * 1024 * 1024


class SandboxFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sandbox"]
    source_id: str
    root: str = "workspace"
    path: str = Field(min_length=1, max_length=4096)


class ConversationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["conversation"]
    source_id: str
    session_id: str
    version_id: str
    path: str = Field(min_length=1, max_length=4096)


FileReference = Annotated[SandboxFile | ConversationFile, Field(discriminator="kind")]


async def read_file(services, viewer_id: str, reference: FileReference):
    def authorize():
        services.capabilities.require_direct_grant(viewer_id, reference.source_id, "file.preview")

    authorize()
    source = services.world.get_card(reference.source_id)
    if reference.kind == "sandbox":
        if not services.plugins.has_trait(source.type, "core.sandbox"):
            raise ResourceValidationError("File source is not a Sandbox")
        try:
            result = await services._require_sandbox_backend().file_operation(
                source.id, "download", root=reference.root, path=reference.path)
        except FileNotFoundError as exc:
            raise ResourceValidationError("File no longer exists") from exc
        except PermissionError as exc:
            raise PermissionDeniedError("File is not readable") from exc
        authorize()
        if result.get("state") != "ready":
            raise ResourceValidationError("File exceeds 16 MiB" if result.get("state") == "oversized"
                                          else result.get("message", "File is not readable"))
        content = base64.b64decode(result["data"], validate=True)
    else:
        if not services.plugins.has_trait(source.type, "core.conversation"):
            raise ResourceValidationError("File source is not a Conversation")
        from backend.conversations.attachments import resolve
        from backend.conversations.models import ConversationAttachmentRef
        attachment = resolve(services, source.id, reference.session_id,
                             [ConversationAttachmentRef(version_id=reference.version_id, path=reference.path)])[0]
        if attachment.size_bytes > MAX_FILE_BYTES:
            raise ResourceValidationError("File exceeds 16 MiB")
        content = bytearray()
        async with aclosing(services.resources.artifacts.read(
                services, source.id, reference.version_id, reference.path)) as stream:
            async for chunk in stream:
                authorize()
                if len(content) + len(chunk) > MAX_FILE_BYTES:
                    raise ResourceValidationError("File exceeds 16 MiB")
                content.extend(chunk)
    authorize()
    if len(content) > MAX_FILE_BYTES:
        raise ResourceValidationError("File exceeds 16 MiB")
    return {"name": reference.path.rsplit("/", 1)[-1], "size_bytes": len(content),
            "data": base64.b64encode(content).decode("ascii")}


async def preview_capability(context, capability, arguments):
    return await context.read_file_preview(capability, arguments)


def register_file_preview(registration):
    from backend.plugins.registry import CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition
    registration.register_capability(CapabilityDefinition(
        kind="file.preview", tool_name="preview_file", description="Read a complete file, up to 16 MiB, from a connected source.",
        input_schema={"type": "object", "properties": {"file": {"type": "object"}}, "required": ["file"], "additionalProperties": False},
    ), preview_capability)
    registration.register_relationship(RelationshipDefinition(
        id="core.file-preview", label="Follow opened files", short_label="preview",
        description="Let this viewer read files from the connected source and follow files opened in its window.",
        source_traits=frozenset({"core.file-viewer"}), target_traits=frozenset({"core.file-source"}),
        capabilities=(CapabilityGrantDefinition(kind="file.preview"),), templateable=True,
    ))
