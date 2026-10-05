"""Declarative, non-executable cards shared by the factory and content Packs."""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator, model_serializer

from backend.card_finishes import CardFinish
from backend.plugins.tutorials import Tutorials, validate_tutorials
from backend.packs.manifest import CreatorMetadata
from backend.packs.face_design import FaceStudio, Mode

Scalar = StrictStr | StrictBool | StrictInt | StrictFloat


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FaceDesign(Model):
    studio: FaceStudio | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        # Keep legacy recipes and saved Legions byte-for-byte compatible in config validation.
        if self.studio is None:
            data.pop("studio", None)
        return data

    title: str = Field(default="我的卡牌", min_length=1, max_length=120)
    description: str = Field(default="填写表单，生成你的内容。", max_length=500)
    variant: Literal["icon", "image", "text", "compact", "dark"] = "icon"
    tone: Literal["midnight", "sage", "sand", "sky", "rose", "stone"] = "sage"
    color: str = Field(default="#617b72", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: Literal["sparkles", "file-text", "calculator", "bot", "layers", "book-open"] = "sparkles"
    finish: CardFinish = "normal"
    layout: Literal["stack", "columns"] = "stack"
    help_text: str = Field(default="", max_length=1000)
    button_label: str = Field(default="运行", min_length=1, max_length=40)


class InputField(Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    type: Literal["text", "number", "boolean"] = "text"
    default: Scalar = ""
    required: bool = False

    @model_validator(mode="after")
    def valid_default(self):
        validate_value(self, self.default)
        return self


def validate_value(field: InputField, value):
    if field.type == "text" and (not isinstance(value, str) or len(value) > 10000):
        raise ValueError(f"{field.label}: 请输入不超过 10000 字的文本")
    if field.type == "number" and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError(f"{field.label}: 请输入有限数值")
    if field.type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{field.label}: 请选择开关值")


class ButtonBinding(Model):
    """Reserved routing data; no custom logic is executed by the current runtime."""
    mode: Mode
    element_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    logic_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class FunctionDesign(Model):
    button_bindings: list[ButtonBinding] | None = Field(default=None, max_length=128)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.button_bindings is None:
            data.pop("button_bindings", None)
        return data

    fields: list[InputField] = Field(default_factory=lambda: [InputField(key="name", label="名称", default="世界")], max_length=20)
    operation: Literal["template", "sum", "multiply", "join"] = "template"
    template: str = Field(default="你好，{{name}}！", max_length=10000)
    separator: str = Field(default="\n", max_length=100)

    @model_validator(mode="after")
    def valid_fields(self):
        targets = [(binding.mode, binding.element_id) for binding in self.button_bindings or []]
        if len(set(targets)) != len(targets):
            raise ValueError("同一视图的按钮不能重复挂载逻辑")
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("字段标识不能重复")
        if self.operation == "template":
            missing = set(re.findall(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}", self.template)) - set(keys)
            if missing:
                raise ValueError("模板引用了不存在的字段: " + ", ".join(sorted(missing)))
        if self.operation in {"sum", "multiply"} and not any(field.type == "number" for field in self.fields):
            raise ValueError("求和或乘积至少需要一个数值字段")
        return self

    def run(self, values: dict[str, Scalar]):
        fields = {field.key: field for field in self.fields}
        if values.keys() - fields.keys():
            raise ValueError("存在未知输入字段")
        resolved = {field.key: values.get(field.key, field.default) for field in self.fields}
        for key, value in resolved.items():
            validate_value(fields[key], value)
            if fields[key].required and isinstance(value, str) and not value.strip():
                raise ValueError(f"{fields[key].label}: 此字段必填")
        if self.operation == "template":
            def replace(match):
                key = match.group(1)
                if key not in resolved:
                    raise ValueError(f"模板引用了不存在的字段: {key}")
                return str(resolved[key])
            result = re.sub(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}", replace, self.template)
        elif self.operation == "join":
            result = self.separator.join(str(value) for value in resolved.values())
        else:
            numbers = [resolved[field.key] for field in self.fields if field.type == "number"]
            if not numbers:
                raise ValueError("求和或乘积至少需要一个数值字段")
            result = sum(numbers) if self.operation == "sum" else math.prod(numbers)
            if not math.isfinite(result):
                raise ValueError("计算结果超出数值范围")
        if len(str(result)) > 200000:
            raise ValueError("输出超过 200000 字符限制")
        return result


class PrintedCard(Model):
    face: FaceDesign = Field(default_factory=FaceDesign)
    function: FunctionDesign = Field(default_factory=FunctionDesign)


class CardRecipe(Model):
    id: str = Field(min_length=1, max_length=128)
    design: PrintedCard
    tutorials: Tutorials = ()

    @model_validator(mode="after")
    def validate_tutorial_content(self):
        validate_tutorials(self.tutorials)
        return self


class PackDesign(Model):
    id: str = Field(default="local.mycards", pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$", max_length=100)
    name: str = Field(default="我的卡包", min_length=1, max_length=120)
    version: str = Field(default="0.1.0", max_length=64)
    creator: CreatorMetadata = Field(default_factory=CreatorMetadata)


class BasketItem(Model):
    kind: Literal["node", "legion", "preset"]
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)


class PackerConfig(Model):
    items: list[BasketItem] = Field(default_factory=list, max_length=50)
    params: dict[str, Scalar] = Field(default_factory=dict, max_length=30)
    include_content: bool = False

    @model_validator(mode="after")
    def valid_params(self):
        for key, value in self.params.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
                raise ValueError("参数标识以小写字母开头，仅包含字母、数字和下划线")
            if isinstance(value, str) and len(value) > 10000:
                raise ValueError("参数文本不能超过 10000 字符")
        return self


class EmptyConfig(Model):
    pass
