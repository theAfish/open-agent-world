"""Portable, bounded drawing data. Never accepts markup, URLs, CSS or scripts."""
from __future__ import annotations

import base64
import binascii
import re
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


class LayerPrint(DrawingModel):
    opacity: float = Field(default=1, ge=0, le=1)
    blend: Literal['normal', 'multiply', 'screen'] = 'normal'


class Box(DrawingModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    x: float = Field(ge=-2048, le=2048)
    y: float = Field(ge=-2048, le=2048)
    width: float = Field(ge=8, le=2048)
    height: float = Field(ge=8, le=2048)
    print: LayerPrint | None = None


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


class FaceButton(DrawingModel):
    action: Literal["open", "collapse", "surface", "delete", "run", "custom"] = "custom"
    background: Color | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    radius: float | None = Field(default=None, ge=0, le=1024)


class FaceElement(Box):
    kind: Literal["title", "subtitle", "description", "icon", "metadata", "status", "tags", "illustration", "badge", "help", "fields", "action", "button", "result", "text"]
    button: FaceButton | None = None
    text: str = Field(default="", max_length=1000)
    font_size: float = Field(default=16, ge=8, le=128)
    color: Color = Field(default="#24382f", pattern=r"^#[0-9a-fA-F]{6}$")
    align: Literal["left", "center", "right"] = "left"
    placement: Literal["slot", "free"] = "slot"
    sizing: Literal["fill", "hug", "fixed"] = "fill"
    pin: Literal["start", "center", "end"] = "start"
    image_png: str = Field(default="", max_length=1_398_126)
    overrides: ElementOverrides = Field(default_factory=lambda: ElementOverrides())

    @model_validator(mode="after")
    def valid_button(self):
        if self.button is not None and self.kind != "button":
            raise ValueError("只有按钮元素可以配置按钮操作")
        return self

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


class ProductionStock(DrawingModel):
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    type: Literal['cotton', 'ivory', 'ink', 'pearl'] = 'ivory'
    grain: float = Field(default=.32, ge=0, le=1)


class ProductionPrint(DrawingModel):
    layered: bool | None = None
    motif: Literal['contour', 'rays', 'grid', 'none'] = 'contour'
    density: float = Field(default=.38, ge=0, le=1)


class PrintFinishing(DrawingModel):
    spotUV: float = Field(default=0, ge=0, le=1)
    foil: float = Field(default=0, ge=0, le=1)
    emboss: float = Field(default=0, ge=0, le=1)
    edgeFoil: float = Field(default=0, ge=0, le=1)
    target: Literal['accents', 'artwork'] = 'accents'
    foilTone: Literal['silver', 'gold'] = 'gold'


class ProductionLaminate(DrawingModel):
    type: Literal['none', 'gloss', 'holo', 'aurora', 'laser', 'starlight'] = 'none'
    strength: float = Field(default=.45, ge=0, le=1)
    roughness: float = Field(default=.38, ge=.06, le=1)


class ProductionMask(DrawingModel):
    source: Literal['all', 'text', 'shapes', 'artwork', 'accents', 'frame', 'elements', 'preset', 'png'] = 'all'
    elementIds: list[str] = Field(default_factory=list, max_length=32)
    preset: Literal['border', 'corners', 'diagonal', 'dots'] = 'border'
    png: str = Field(default='', max_length=1_398_126)
    fit: Literal['contain', 'cover', 'stretch'] | None = None
    channel: Literal['alpha', 'luminance'] = 'alpha'
    invert: bool = False

    @field_validator('png')
    @classmethod
    def png_image(cls, value):
        return validate_png(value) if value else value

    @field_validator('elementIds')
    @classmethod
    def bounded_ids(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', value) for value in values):
            raise ValueError('Mask element identifiers must be unique drawing identifiers')
        return values

    @model_validator(mode='after')
    def image_required(self):
        if self.source == 'png' and not self.png:
            raise ValueError('A PNG mask requires an embedded PNG image')
        return self


class PrintContent(DrawingModel):
    source: Literal['all', 'elements'] = 'elements'
    elementIds: list[str] = Field(default_factory=list, max_length=56)

    @field_validator('elementIds')
    @classmethod
    def bounded_ids(cls, values):
        return ProductionMask.bounded_ids(values)


class LayerPattern(DrawingModel):
    motif: Literal['contour', 'rays', 'grid', 'none'] = 'none'
    density: float = Field(default=.38, ge=0, le=1)


class ProductionLayer(DrawingModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    kind: Literal['ink', 'laminate', 'foil', 'emboss', 'uv']
    enabled: bool = True
    strength: float = Field(default=.7, ge=0, le=1)
    roughness: float = Field(default=.38, ge=.06, le=1)
    color: str = Field(default='#d6ae61', pattern=r'^#[0-9a-fA-F]{6}$')
    film: Literal['gloss', 'holo', 'aurora', 'laser', 'starlight'] = 'holo'
    relief: Literal['raised', 'recessed'] = 'raised'
    mask: ProductionMask = Field(default_factory=ProductionMask)
    content: PrintContent | None = None
    pattern: LayerPattern | None = None
    blend: Literal['normal', 'multiply', 'screen'] | None = None

    @model_validator(mode='after')
    def ink_pattern_only(self):
        if self.pattern is not None and self.kind != 'ink':
            raise ValueError('Only ink layers can print a background pattern')
        return self


class CardProduction(DrawingModel):
    version: Literal[1] = 1
    stock: ProductionStock = Field(default_factory=ProductionStock)
    print: ProductionPrint = Field(default_factory=ProductionPrint)
    finishing: PrintFinishing = Field(default_factory=PrintFinishing)
    laminate: ProductionLaminate = Field(default_factory=ProductionLaminate)
    # Omitted retains the legacy optical pair; an explicit empty list means no passes.
    layers: list[ProductionLayer] | None = Field(default=None, max_length=24)

    @model_validator(mode='after')
    def unique_process_ids(self):
        ids = [layer.id for layer in self.layers or []]
        if len(ids) != len(set(ids)):
            raise ValueError('Production layer identifiers must be unique')
        return self


class SurfaceRecipe(DrawingModel):
    production: CardProduction | None = None
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
        production = self.design.production if self.design else None
        passes = [layer.content for layer in (production.layers or []) if layer.content] if production and production.print.layered else []
        assigned = {identifier for content in passes if content.source == 'elements' for identifier in content.elementIds}
        groups = [[item for item in self.elements if item.id not in assigned]]
        groups.extend([item for item in self.elements if item.id in content.elementIds] for content in passes if content.source == 'elements')
        for group in groups:
            kinds = [item.kind for item in group if item.kind not in ('text', 'illustration', 'button')]
            if len(set(kinds)) != len(kinds):
                raise ValueError("同一工艺层内的绑定内容和功能区域不能重复")
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
