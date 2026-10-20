"""Declarative, host-owned private document resources for Agents."""
from pydantic import BaseModel, ConfigDict, Field


class DefaultEquipment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    type: str = Field(min_length=1, max_length=128)
    relationship: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=200)
