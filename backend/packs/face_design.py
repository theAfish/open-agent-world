"""Portable, bounded drawing data. Never accepts markup, URLs, CSS or scripts."""
from __future__ import annotations

import base64
import binascii
import struct
import zlib
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

Mode = Literal["node", "preview", "inspector", "workspace"]
Color = str
PNG_LIMIT = 1024 * 1024


class DrawingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_serializer(mode="wrap")
    def without_optional_nulls(self, handler):
        # Optional authoring metadata must not add null overrides to portable v1 designs.
        data = {key: value for key, value in handler(self).items() if value is not None}
        for key in ('placement', 'sizing', 'pin', 'image_png', 'overrides'):
            if key not in self.model_fields_set:
                data.pop(key, None)
        return data


class Point(DrawingModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Box(DrawingModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    x: float = Field(ge=-2048, le=2048)
    y: float = Field(ge=-2048, le=2048)
    width: float = Field(ge=8, le=2048)
    height: float = Field(ge=8, le=2048)


class Shape(Box):
    kind: Literal["rect", "ellipse", "polygon"] = "rect"
    radius: float = Field(default=20, ge=0, le=1024)
    fill: Color = Field(default="#dfe8df", pattern=r"^#[0-9a-fA-F]{6}$")
    points: list[Point] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def polygon(self):
        if self.kind == "polygon" and len(self.points) < 3:
            raise ValueError("多边形至少需要三个顶点")
        return self


class FaceElement(Box):
    kind: Literal["title", "subtitle", "description", "icon", "metadata", "status", "tags", "illustration", "badge", "help", "fields", "action", "result", "text"]
    text: str = Field(default="", max_length=1000)
    font_size: float = Field(default=16, ge=8, le=128)
    color: Color = Field(default="#24382f", pattern=r"^#[0-9a-fA-F]{6}$")
    align: Literal["left", "center", "right"] = "left"
    placement: Literal["slot", "free"] = "slot"
    sizing: Literal["fill", "hug", "fixed"] = "fill"
    pin: Literal["start", "center", "end"] = "start"
    image_png: str = Field(default="", max_length=1_398_126)
    overrides: ElementOverrides = Field(default_factory=lambda: ElementOverrides())

    @field_validator("image_png")
    @classmethod
    def image(cls, value):
        return validate_png(value) if value else value


class ElementOverrides(DrawingModel):
    font_size: float | None = Field(default=None, ge=8, le=128)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    align: Literal["left", "center", "right"] | None = None


class DesignTokens(DrawingModel):
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    surface: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    text: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    muted: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    border: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    radius: float | None = Field(default=None, ge=0, le=1024)
    margin: float | None = Field(default=None, ge=0, le=512)
    gap: float | None = Field(default=None, ge=0, le=128)
    title_size: float | None = Field(default=None, ge=8, le=128)
    body_size: float | None = Field(default=None, ge=8, le=128)


class MaterialSettings(DrawingModel):
    type: Literal["none", "matte", "foil", "holo", "starlight", "iridescent"] = "none"
    intensity: float = Field(default=.38, ge=0, le=1)
    mask: Literal["all", "edges", "visual"] = "visual"
    roughness: float = Field(default=.32, ge=.08, le=1)


class SurfaceRecipe(DrawingModel):
    field_layout: Literal["stack", "columns"] | None = None
    recipe: Literal["hero", "compact", "split", "badge", "editorial", "utility", "minimal", "poster"] = "hero"
    kit: Literal["sand", "paper", "ink", "ceramic", "industrial", "playful"] = "sand"
    appearance: Literal["light", "dark"] = "light"
    softness: float = Field(default=.6, ge=0, le=1)
    density: Literal["low", "medium", "high"] = "medium"
    emphasis: Literal["balanced", "title", "visual"] = "balanced"
    alignment: Literal["left", "center", "right"] = "left"
    material: MaterialSettings = Field(default_factory=MaterialSettings)
    tokens: DesignTokens = Field(default_factory=DesignTokens)


@lru_cache(maxsize=8)
def validate_png(value: str) -> str:
    prefix = "data:image/png;base64,"
    if not value.startswith(prefix):
        raise ValueError("背景只接受内嵌 PNG 图片")
    try:
        data = base64.b64decode(value[len(prefix):], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PNG 编码无效") from exc
    if len(data) > PNG_LIMIT or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG 无效或超过 1 MiB")
    offset, kinds = 8, []
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG 数据不完整")
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:end - 4]
        if zlib.crc32(kind + payload) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError("PNG 校验失败")
        if not kinds:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG 缺少尺寸信息")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if not (1 <= width <= 2048 and 1 <= height <= 2048) or depth not in depths.get(color, set()) or compression or filtering or interlace not in (0, 1):
                raise ValueError("PNG 尺寸不得超过 2048 × 2048，且必须使用标准编码")
        kinds.append(kind)
        offset = end
        if kind == b"IEND":
            if length or offset != len(data) or b"IDAT" not in kinds:
                raise ValueError("PNG 结尾无效")
            return value
    raise ValueError("PNG 数据不完整")


class SurfaceDesign(DrawingModel):
    design: SurfaceRecipe | None = None
    width: int = Field(ge=96, le=2048)
    height: int = Field(ge=96, le=2048)
    preset: Literal["icon", "image", "text", "compact", "dark"] = "icon"
    tone: Literal["midnight", "sage", "sand", "sky", "rose", "stone"] = "sage"
    field_layout: Literal["stack", "columns"] = "stack"
    shapes: list[Shape] = Field(min_length=1, max_length=24)
    elements: list[FaceElement] = Field(default_factory=list, max_length=32)
    background_png: str = Field(default="", max_length=1_398_126)
    image_fit: Literal["contain", "cover", "stretch"] = "contain"
    image_shape: bool = True

    @field_validator("background_png")
    @classmethod
    def png(cls, value):
        return validate_png(value) if value else value

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [item.id for item in [*self.shapes, *self.elements]]
        if len(set(ids)) != len(ids):
            raise ValueError("卡面元素标识不能重复")
        kinds = [item.kind for item in self.elements if item.kind != "text"]
        if len(set(kinds)) != len(kinds):
            raise ValueError("绑定内容和功能区域不能重复")
        return self


class FaceStudio(DrawingModel):
    version: Literal[1] = 1
    enabled: list[Mode] = Field(min_length=1, max_length=4)
    initial: Mode = "preview"
    open: Mode = "workspace"
    modes: dict[Mode, SurfaceDesign] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def valid_modes(self):
        if len(set(self.enabled)) != len(self.enabled) or self.initial not in self.enabled or self.open not in self.enabled:
            raise ValueError("初始模式和打开模式必须在允许的模式中")
        if set(self.enabled) - self.modes.keys():
            raise ValueError("每个允许的模式都需要卡面设计")
        return self
