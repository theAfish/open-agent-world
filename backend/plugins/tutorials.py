"""Declarative, offline tutorial content shared by cards and Packs."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

Text = Annotated[str, Field(min_length=1, max_length=100_000)]
LocalizedText = Text | dict[Annotated[str, Field(min_length=1, max_length=32)], Text]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]*$", max_length=128)]


class TutorialStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Identifier
    title: LocalizedText
    body: LocalizedText


class TutorialDefinition(BaseModel):
    """IDs are local to the owning card type or Pack, not card instances.

    Strings use the author's language; maps require an English fallback.
    Empty steps plus a document define a reference-only article.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Identifier
    revision: int = Field(default=1, ge=1, strict=True)
    title: LocalizedText
    summary: LocalizedText
    trigger: Literal["encounter", "manual"] = "encounter"
    steps: tuple[TutorialStep, ...] = Field(default=(), max_length=50)
    document: LocalizedText | None = None
    after: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if not self.steps and self.document is None:
            raise ValueError("A tutorial needs steps or a document")
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Tutorial step IDs must be unique")
        for value in [self.title, self.summary, self.document,
                      *(text for step in self.steps for text in (step.title, step.body))]:
            if isinstance(value, dict) and "en" not in value:
                raise ValueError("Localized tutorial text requires an en fallback")
        return self


Tutorials = Annotated[tuple[TutorialDefinition, ...], Field(max_length=50)]
_TUTORIALS_ADAPTER = TypeAdapter(Tutorials)


def validate_tutorials(value: object) -> tuple[TutorialDefinition, ...]:
    tutorials = _TUTORIALS_ADAPTER.validate_python(value)
    by_id = {item.id: item for item in tutorials}
    if len(by_id) != len(tutorials):
        raise ValueError("Tutorial IDs must be unique within their owner")
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key not in by_id:
            raise ValueError("Tutorial prerequisites must reference the same owner")
        if key in visiting:
            raise ValueError("Tutorial prerequisites cannot contain cycles")
        if key in visited:
            return
        visiting.add(key)
        for dependency in by_id[key].after:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in by_id:
        visit(key)
    return tutorials
