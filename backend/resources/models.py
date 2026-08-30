from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.world.models import CardType


class ResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    kind: CardType
    filename: str
    relative_path: str
    media_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    revision: int
    created_at: datetime
    updated_at: datetime


class TextDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    filename: str
    content: str
    size_bytes: int
    revision: int
    updated_at: datetime


class TextReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    expected_revision: Annotated[int | None, Field(ge=1)] = None


class TextEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    text: str

    @model_validator(mode="after")
    def validate_range(self) -> "TextEdit":
        if self.end < self.start:
            raise ValueError("edit end must be greater than or equal to start")
        return self


class TextPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edits: Annotated[list[TextEdit], Field(min_length=1, max_length=1000)]
    expected_revision: Annotated[int | None, Field(ge=1)] = None


class ImageImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    media_type: str
    data_base64: str


class ResourceRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int
    operation: str
    old_sha256: str | None
    new_sha256: str
    actor_id: str | None
    created_at: datetime
