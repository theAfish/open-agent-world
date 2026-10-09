"""Provider-neutral visual tool output. SDK objects never enter host services."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from backend.errors import ResourceValidationError


@dataclass(frozen=True)
class ToolImage:
    data: bytes = field(repr=False)
    media_type: str

    def __post_init__(self):
        from backend.resources.manager import ManagedResourceStore
        if not self.data or len(self.data) > 8 * 1024 * 1024:
            raise ResourceValidationError("Tool image must be between 1 byte and 8 MiB")
        media_type, width, height = ManagedResourceStore._inspect_image(self.data)
        if media_type != self.media_type or width * height > 16_777_216:
            raise ResourceValidationError("Invalid tool image media type or dimensions")

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass(frozen=True)
class VisualToolResult:
    """Metadata is safe for activity logs; image bytes go only to the model."""
    metadata: dict[str, Any]
    images: tuple[ToolImage, ...]

    def __post_init__(self):
        if not 1 <= len(self.images) <= 4 or sum(len(image.data) for image in self.images) > 8 * 1024 * 1024:
            raise ResourceValidationError("Visual tool results allow 1-4 images totaling at most 8 MiB")

    def summary(self) -> dict[str, Any]:
        return {**self.metadata, "images": [
            {"media_type": image.media_type, "size_bytes": len(image.data)} for image in self.images
        ]}


def codex_tool_content(result: Any) -> list[dict[str, Any]]:
    """App Server dynamic tool wire format, including actual visual inputs."""
    if isinstance(result, VisualToolResult):
        return [{"type": "inputText", "text": json.dumps(result.summary(), ensure_ascii=False)},
                *[{"type": "inputImage", "imageUrl": image.data_url} for image in result.images]]
    return [{"type": "inputText", "text": json.dumps(result, ensure_ascii=False)}]


def adk_tool_result(result: Any) -> Any:
    if not isinstance(result, VisualToolResult):
        # Validate while still inside the tool boundary, before ADK persists or
        # constructs a model response from this plugin-owned payload.
        json.dumps(result, allow_nan=False)
        return result
    summary = result.summary()
    json.dumps(summary, allow_nan=False)
    from google.genai import types
    return {"result": summary, "media": [
        types.Part.from_bytes(data=image.data, mime_type=image.media_type) for image in result.images
    ]}
