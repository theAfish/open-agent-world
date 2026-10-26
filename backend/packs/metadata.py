"""Shared Pack identity and creator metadata, independent of source/archive models."""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.plugins.tutorials import Tutorials, validate_tutorials

PACK_ID = r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$"


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
