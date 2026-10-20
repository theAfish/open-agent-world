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


def durable_tool_media(value: Any) -> tuple[Any, list[str]]:
    """Project JSON-shaped ADK tool observations to metadata for persistence.

    User-supplied images, ordinary tool data and provider signatures are left
    intact. Only native FunctionResponse image parts and OAW's pre-extraction
    visual-result envelope contain transient tool observations. Never mutate the
    live request; it still needs the real image bytes.
    """
    omitted: list[str] = []

    def image_metadata(part):
        if not isinstance(part, dict):
            return None
        blob = part.get("inline_data", part.get("inlineData"))
        if not isinstance(blob, dict):
            return None
        mime = blob.get("mime_type", blob.get("mimeType", ""))
        if not isinstance(mime, str) or not mime.startswith("image/") or blob.get("data") is None:
            return None
        data = blob["data"]
        if isinstance(data, dict) and data.get("encoding") == "base64":
            data = data.get("data")
        size = len(data) if isinstance(data, bytes) else None
        if isinstance(data, str):
            try:
                size = len(base64.b64decode(data, altchars=b"-_", validate=True))
            except ValueError:
                pass
        return {"media_type": mime, "size_bytes": size, "omitted": True,
                "reason": "Transient tool image; observe again if its pixels are needed."}

    def filter_images(parts, path):
        kept, metadata = [], []
        for index, part in enumerate(parts):
            location = f"{path}[{index}]"
            info = image_metadata(part)
            if info is None:
                kept.append(walk(part, location))
            else:
                metadata.append(info)
                omitted.append(location)
        return kept, metadata

    def walk(item, path):
        if isinstance(item, list):
            return [walk(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            location = path + "." + key
            if key in {"function_response", "functionResponse"} and isinstance(child, dict):
                response = walk(child, location)
                if isinstance(child.get("parts"), list):
                    kept, metadata = filter_images(child["parts"], location + ".parts")
                    if metadata:
                        response["parts"] = kept
                        response["response"] = {**response.get("response", {}),
                                                "oaw_omitted_tool_images": metadata}
                result[key] = response
            elif key == "media" and "result" in item and isinstance(child, list):
                kept, metadata = filter_images(child, location)
                result[key] = kept
                if metadata:
                    result["oaw_omitted_tool_images"] = metadata
            else:
                result[key] = walk(child, location)
        return result

    return walk(value, "$"), omitted
