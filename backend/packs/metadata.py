"""Shared Pack identity and creator metadata, independent of source/archive models."""
import base64
import binascii
import re
from typing import Literal, Self
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from backend.plugins.tutorials import Tutorials, validate_tutorials

PACK_ID = r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$"
PREVIEW_IMAGE_LIMIT = 256 * 1024
PREVIEW_URL_LIMIT = 4 * ((PREVIEW_IMAGE_LIMIT + 2) // 3) + 64
PREVIEW_ICON = r"^[a-z][a-z0-9-]{0,63}$"


def preview_image_media_type(content: bytes) -> str:
    """Accept bounded image bytes without fetching URLs or executing SVG content."""
    if not 0 < len(content) <= PREVIEW_IMAGE_LIMIT:
        raise ValueError("Preview images require 1 byte to 256 KiB")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    try:
        text = content.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
            raise ValueError("Preview SVG declarations are not allowed")
        root = ElementTree.fromstring(text)
    except (UnicodeDecodeError, ElementTree.ParseError) as error:
        raise ValueError("Preview content must be a supported image") from error
    if root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
        raise ValueError("Preview content must be an SVG image")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise ValueError("Active preview SVG content is not allowed")
        values = list(element.attrib.values()) + ([element.text] if tag == "style" and element.text else [])
        for key, value in element.attrib.items():
            attribute = key.rsplit("}", 1)[-1].lower()
            if attribute.startswith("on"):
                raise ValueError("Preview SVG events and external references are not allowed")
            if attribute in {"href", "src"} and value and not value.startswith("#"):
                if not re.match(r"data:image/(?:png|jpeg|webp|gif);base64,", value):
                    raise ValueError("Preview SVG images must be embedded raster images")
                validate_preview_image_url(value)
        for value in values:
            if re.search(r"@import|expression\s*\(", value, re.IGNORECASE):
                raise ValueError("External preview SVG styles are not allowed")
            for reference in re.findall(r"url\s*\(([^)]*)\)", value, re.IGNORECASE):
                if not reference.strip(" \t\r\n\"'").startswith("#"):
                    raise ValueError("External preview SVG images are not allowed")
    return "image/svg+xml"


def validate_preview_image_url(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > PREVIEW_URL_LIMIT:
        raise ValueError("Preview image URL is too large")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif|svg\+xml));base64,([A-Za-z0-9+/]*={0,2})", value)
    if match is None:
        raise ValueError("Preview images must be embedded image data URLs")
    try:
        content = base64.b64decode(match[2], validate=True)
    except binascii.Error as error:
        raise ValueError("Preview image has invalid base64 encoding") from error
    if preview_image_media_type(content) != match[1]:
        raise ValueError("Preview image media type does not match its content")
    return value


class PackVisualPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    icon: str | None = Field(default=None, pattern=PREVIEW_ICON)
    icon_url: str | None = Field(default=None, max_length=PREVIEW_URL_LIMIT)
    artwork_url: str | None = Field(default=None, max_length=PREVIEW_URL_LIMIT)

    _images = field_validator("icon_url", "artwork_url")(validate_preview_image_url)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class CreatorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tutorials: Tutorials = ()

    @model_validator(mode="after")
    def validate_tutorial_content(self) -> Self:
        validate_tutorials(self.tutorials)
        return self

    description: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=120)
    preparation: str = Field(default="", max_length=2000)
    example: str = Field(default="", max_length=2000)
    expected_result: str = Field(default="", max_length=2000)
    accent_color: str = Field(default="#617b72", pattern=r"^#[0-9a-fA-F]{6}$")
    packaging: Literal["standard", "premium", "paper", "collector"] = "standard"
