"""Bounded research strategies from held metadata/abstracts, never MethodSpecs.

This is the caller input contract. The host binds source identity, revisions,
metadata snapshots and actor, and always records a non-executable draft.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MicroSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=7, max_length=128, pattern=r"^micro-[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=500)
    paper_id: str = Field(min_length=1, max_length=200)
    paper_revision: int = Field(ge=0, strict=True)
    basis: Literal["metadata", "abstract"]
    purpose: str = Field(min_length=1, max_length=10_000)
    steps: list[str] = Field(min_length=1, max_length=50)
    missing: list[str] = Field(min_length=1, max_length=50)

    @field_validator("id", "name", "paper_id", "purpose")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Micro-Skill fields must contain meaningful text")
        return value

    @field_validator("steps", "missing")
    @classmethod
    def bounded_lines(cls, values):
        if any(not item.strip() or len(item) > 5000 for item in values):
            raise ValueError("Micro-Skill steps and missing information require nonblank text of at most 5000 characters")
        return values


CLAIM_SCOPE = ("Research/search strategy based on stored metadata or source abstract; "
               "not a reconstructed experimental method, executable implementation, or scientific verification.")
