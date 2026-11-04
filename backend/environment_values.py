"""Host-side document models; isolated runtime workers remain stdlib-only."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_serializer


class EnvironmentOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["replace", "append", "prepend"] = "replace"
    separator: StrictStr | None = None

    @field_validator("separator")
    @classmethod
    def validate_separator(cls, value):
        if value is not None and ("\0" in value or len(value) > 100):
            raise ValueError("Environment separators must be NUL-free and at most 100 characters")
        return value

    @model_serializer(mode="wrap")
    def serialize_operation(self, handler):
        result = handler(self)
        if self.operation == "replace":
            result.pop("operation", None)
        if self.separator is None:
            result.pop("separator", None)
        return result


class EnvironmentValue(EnvironmentOperation):
    value: StrictStr
