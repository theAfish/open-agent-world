"""Structured paper content (schema 1.0).

Every extracted element may carry ``loc`` (page plus a normalized 0-1 bbox) so
the reader can highlight the source. ``provenance`` says who produced it:
GROBID (see grobid.py), then optionally an Agent or a person. There are no
per-field confidences; GROBID does not report them.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Loc(Model):
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description="x0, y0, x1, y1 in 0-1 page units")


class Author(Model):
    name: str
    orcid: str | None = None
    affiliation_ids: list[str] = Field(default_factory=list)
    corresponding: bool = False
    email: str | None = None


class Affiliation(Model):
    id: str
    text: str
    country: str | None = None


class Venue(Model):
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    issn: str | None = None


class Identifiers(Model):
    doi: str | None = None
    arxiv: str | None = None
    pmid: str | None = None


class Dates(Model):
    received: str | None = None
    revised: str | None = None
    accepted: str | None = None
    published: str | None = None


class Metadata(Model):
    title: str | None = None
    subtitle: str | None = None
    article_type: str | None = Field(default=None, description="research, review, letter, communication, perspective, ...")
    authors: list[Author] = Field(default_factory=list)
    affiliations: list[Affiliation] = Field(default_factory=list)
    venue: Venue = Field(default_factory=Venue)
    identifiers: Identifiers = Field(default_factory=Identifiers)
    dates: Dates = Field(default_factory=Dates)
    keywords: list[str] = Field(default_factory=list)
    license: str | None = None
    language: str | None = None


class LabeledText(Model):
    heading: str
    text: str


class Abstract(Model):
    text: str = ""
    sections: list[LabeledText] = Field(default_factory=list, description="Structured abstracts: Background, Methods, ...")
    loc: Loc | None = None


class Block(Model):
    type: Literal["paragraph", "list", "equation", "quote", "code"] = "paragraph"
    text: str
    number: str | None = None
    latex: str | None = None
    citations: list[str] = Field(default_factory=list, description="Reference ids, e.g. r3")
    loc: Loc | None = None


class Section(Model):
    id: str
    level: int = Field(default=1, ge=1, le=6)
    number: str | None = None
    heading: str
    blocks: list[Block] = Field(default_factory=list)
    loc: Loc | None = None


class Figure(Model):
    id: str
    kind: Literal["figure", "scheme", "chart"] = "figure"
    label: str
    caption: str
    subfigures: list[str] = Field(default_factory=list)
    image: str | None = Field(default=None, description="Object key of the cropped image")
    loc: Loc | None = None


class Table(Model):
    id: str
    label: str
    caption: str
    header: list[list[str]] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)
    loc: Loc | None = None


class Funding(Model):
    agency: str
    grant: str | None = None


class BackMatter(Model):
    acknowledgments: str | None = None
    funding: list[Funding] = Field(default_factory=list)
    funding_statement: str | None = None
    author_contributions: str | None = None
    conflicts: str | None = None
    data_availability: str | None = None
    code_availability: str | None = None
    ethics: str | None = None
    abbreviations: str | None = None
    appendices: list[Section] = Field(default_factory=list)


class Reference(Model):
    id: str
    raw: str
    authors: list[str] = Field(default_factory=list)
    title: str | None = None
    venue: str | None = None
    year: int | None = None
    volume: str | None = None
    pages: str | None = None
    doi: str | None = None
    loc: Loc | None = None


class Provenance(Model):
    extractor: str = Field(description="grobid/<version>, suffixed +agent or +user after revisions")
    warnings: list[str] = Field(default_factory=list, description="Known gaps in this extraction")


class PaperStructure(Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    metadata: Metadata = Field(default_factory=Metadata)
    abstract: Abstract = Field(default_factory=Abstract)
    highlights: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list, description="Flat reading order; level gives nesting")
    figures: list[Figure] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    back_matter: BackMatter = Field(default_factory=BackMatter)
    references: list[Reference] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)
    domain: dict[str, Any] = Field(default_factory=dict, description="Field-specific facts, e.g. materials and properties")
    provenance: Provenance
