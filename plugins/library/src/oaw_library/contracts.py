"""Additive Library contracts and pure identity/anchor helpers.

These models neither fetch sources nor mutate a Paper. Document revisions are
optimistic locks; a DocumentVersion identifies the immutable uploaded PDF bytes.
An anchor's status describes location freshness, never scientific verification.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from datetime import datetime
from typing import Annotated, Literal, Mapping, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Sha256 = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
AnchorStatus = Literal["current", "needs_relocation", "missing"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def normalize_source_url(value: str | None) -> str | None:
    """Validate a source link without making a network request."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Source URL must be a string")
    value = value.strip()
    if len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise ValueError("Source URL contains invalid characters or is too long")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Source URL must use http or https and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Source URL must not contain credentials")
        parsed.port  # Validate malformed/out-of-range ports without fetching.
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            hostname = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
            if len(hostname) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.I) for label in hostname.split(".")):
                raise ValueError("Source URL contains an invalid host")
    except ValueError as error:
        raise ValueError(f"Invalid source URL: {error}") from error
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def normalize_doi(value: str | None) -> str | None:
    """Canonical DOI identity; accept DOI resolver URLs, not arbitrary URLs."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("DOI must be a string")
    identifier = value.strip()
    if identifier.lower().startswith("doi:"):
        identifier = identifier[4:].strip()
    elif "://" in identifier:
        normalize_source_url(identifier)
        parsed = urlsplit(identifier)
        if parsed.hostname.lower() not in {"doi.org", "dx.doi.org"} or parsed.query or parsed.fragment:
            raise ValueError("DOI URL must be an unambiguous doi.org resolver URL")
        identifier = unquote(parsed.path.lstrip("/"))
    if len(identifier) > 2048 or not re.fullmatch(r"10\.\d{4,9}/[^\s\x00-\x1f\x7f]+", identifier):
        raise ValueError("Invalid DOI")
    return identifier.lower()


def normalize_external_ids(values: Mapping[str, str] | None) -> dict[str, str]:
    """Normalize explicitly supported identity namespaces; titles are not IDs."""
    if values is None:
        return {}
    if not isinstance(values, Mapping) or len(values) > 20:
        raise ValueError("External IDs must be a bounded object")
    result: dict[str, str] = {}
    for scheme, value in values.items():
        if not isinstance(scheme, str) or not isinstance(value, str):
            raise ValueError("External identity names and values must be strings")
        scheme, value = scheme.strip().lower(), value.strip()
        if len(value) > 512:
            raise ValueError("External ID is too long")
        if scheme == "openalex":
            value = re.sub(r"^https://openalex\.org/", "", value, flags=re.I).upper()
            valid = bool(re.fullmatch(r"W[1-9]\d*", value))
        elif scheme == "arxiv":
            value = re.sub(r"^(?:arxiv:|https://arxiv\.org/(?:abs|pdf)/)", "", value, flags=re.I).removesuffix(".pdf").lower()
            valid = bool(re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*(?:\.[a-z]{2})?/\d{7})(?:v[1-9]\d*)?", value))
        elif scheme == "pmid":
            valid = bool(re.fullmatch(r"[1-9]\d*", value))
        elif scheme == "pmcid":
            value = value.upper()
            valid = bool(re.fullmatch(r"PMC[1-9]\d*", value))
        elif scheme == "semantic_scholar":
            value = value.lower()
            valid = bool(re.fullmatch(r"[0-9a-f]{40}|corpusid:[1-9]\d*", value))
        else:
            raise ValueError(f"Unsupported external ID namespace: {scheme}")
        if not valid:
            raise ValueError(f"Invalid {scheme} identity")
        if scheme in result and result[scheme] != value:
            raise ValueError(f"Conflicting {scheme} identities")
        result[scheme] = value
    return dict(sorted(result.items()))


class PaperMetadata(ContractModel):
    title: str = Field(default="", max_length=2000)
    authors: list[ShortText] = Field(default_factory=list, max_length=300)
    year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    doi: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    source_abstract: str = Field(default="", max_length=100_000)
    abstract_source_url: str | None = None
    agent_abstract: str = Field(default="", max_length=100_000)
    source_url: str | None = None

    @field_validator("year", mode="before")
    @classmethod
    def legacy_year_string(cls, value):
        # Existing PaperConfig stores year as text. Empty means unknown.
        if value == "":
            return None
        return int(value.strip()) if isinstance(value, str) and re.fullmatch(r"\d{4}", value.strip()) else value

    @field_validator("doi", mode="before")
    @classmethod
    def canonical_doi(cls, value):
        return normalize_doi(value)

    @field_validator("external_ids", mode="before")
    @classmethod
    def canonical_external_ids(cls, value):
        return normalize_external_ids(value)

    @field_validator("source_url", "abstract_source_url", mode="before")
    @classmethod
    def safe_link(cls, value):
        return normalize_source_url(value)


def paper_identity_keys(metadata: PaperMetadata | Mapping) -> tuple[str, ...]:
    """Strong lookup keys only; their conflict resolution remains a host policy."""
    value = PaperMetadata.model_validate(metadata)
    keys = ([f"doi:{value.doi}"] if value.doi else [])
    keys.extend(f"{scheme}:{identifier}" for scheme, identifier in value.external_ids.items())
    return tuple(keys)


def _aware_timestamp(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Timestamp must include a timezone; unknown timestamps may be null")
    return value


class DocumentVersion(ContractModel):
    id: Sha256 | None = None
    sha256: Sha256
    filename: str = Field(default="paper.pdf", min_length=1, max_length=200)
    pages: int = Field(ge=1, le=1_000_000, strict=True)
    imported_at: datetime | None = None
    text_parser_version: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["main", "supplement"] = "main"
    version_label: str = Field(default="", max_length=200)

    @field_validator("imported_at")
    @classmethod
    def import_timezone(cls, value):
        return _aware_timestamp(value)

    @model_validator(mode="after")
    def content_identity(self):
        if self.id is not None and self.id != self.sha256:
            raise ValueError("Document version ID must equal its original-byte SHA-256")
        object.__setattr__(self, "id", self.sha256)
        return self


class TextItemRange(ContractModel):
    text_item_index: int = Field(ge=0, strict=True)
    start_offset: int = Field(ge=0, strict=True)
    end_offset: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def ordered_offsets(self):
        if self.end_offset <= self.start_offset:
            raise ValueError("Text range must have a positive, exclusive-end UTF-16 span")
        return self


def quote_sha256(text: str) -> str:
    """Hash exact Unicode quote encoded as UTF-8, without normalizing it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceAnchor(ContractModel):
    id: str | None = Field(default=None, min_length=1, max_length=128)
    paper_id: str | None = Field(default=None, min_length=1, max_length=200)
    document_version_id: Sha256
    document_sha256: Sha256
    page: int = Field(ge=1, strict=True)
    text_parser_version: str | None = Field(default=None, min_length=1, max_length=200)
    text_offset_unit: Literal["utf16"] = "utf16"
    text_ranges: list[TextItemRange] = Field(default_factory=list, max_length=1000)
    quote: str = Field(default="", max_length=100_000)
    quote_sha256: Sha256 | None = None
    rects: list[tuple[float, float, float, float]] = Field(default_factory=list, max_length=500)
    coordinate_space: Literal["pdfjs-default-viewport-normalized-v1"] = "pdfjs-default-viewport-normalized-v1"
    page_rotation: Literal[0, 90, 180, 270] = 0
    status: AnchorStatus = "current"

    @field_validator("rects", mode="before")
    @classmethod
    def normalized_rectangles(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Anchor rectangles must be a list")
        for rect in value:
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                raise ValueError("Anchor rectangle must contain x, y, width, height")
            if any(isinstance(part, bool) or not isinstance(part, (int, float)) or not math.isfinite(part) for part in rect):
                raise ValueError("Anchor rectangle coordinates must be finite numbers")
            x, y, width, height = rect
            if min(x, y) < 0 or width <= 0 or height <= 0 or x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
                raise ValueError("Anchor rectangle must stay inside the normalized page")
        return value

    @model_validator(mode="after")
    def source_identity(self):
        if self.document_version_id != self.document_sha256:
            raise ValueError("Anchor document version and document hash disagree")
        if self.quote_sha256 is not None and self.quote_sha256 != quote_sha256(self.quote):
            raise ValueError("Anchor quote SHA-256 does not match the exact quote")
        if self.quote and self.quote_sha256 is None:
            object.__setattr__(self, "quote_sha256", quote_sha256(self.quote))
        if self.text_ranges and not self.text_parser_version:
            raise ValueError("Text ranges require the parser/text-index version")
        return self


def source_anchor_status(
    anchor: SourceAnchor | Mapping,
    document: DocumentVersion | Mapping | None,
    *,
    text_parser_version: str | None = None,
    text_items: Sequence[str] | None = None,
    resolved_quote: str | None = None,
) -> AnchorStatus:
    """Check a location against its referenced version, not the active version.

    A current geometry-only anchor is still geometry-only. Quote equality does
    not establish whether a source supports a scientific claim. Callers should
    supply resolved_quote only after reconstructing exact selection semantics;
    joining PDF items with guessed whitespace would not be an exact check.
    """
    anchor = SourceAnchor.model_validate(anchor)
    if document is None:
        return "missing"
    document = DocumentVersion.model_validate(document)
    if anchor.document_version_id != document.id or anchor.document_sha256 != document.sha256 or anchor.page > document.pages:
        return "needs_relocation"
    if text_parser_version is not None and anchor.text_parser_version and anchor.text_parser_version != text_parser_version:
        return "needs_relocation"
    if resolved_quote is not None and anchor.quote_sha256 is not None and quote_sha256(resolved_quote) != anchor.quote_sha256:
        return "needs_relocation"
    if text_items is not None:
        for selected in anchor.text_ranges:
            if selected.text_item_index >= len(text_items):
                return "needs_relocation"
            text = text_items[selected.text_item_index]
            if not isinstance(text, str):
                return "needs_relocation"
            try:
                raw = text.encode("utf-16-le")
            except UnicodeEncodeError:
                return "needs_relocation"
            if selected.end_offset * 2 > len(raw):
                return "needs_relocation"
            try:
                raw[selected.start_offset * 2:selected.end_offset * 2].decode("utf-16-le")
            except UnicodeDecodeError:
                return "needs_relocation"
    return "current"


class ResearchBudget(ContractModel):
    max_searches: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    max_papers: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    max_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_duration_seconds: int | None = Field(default=None, ge=0, strict=True)
    max_parallelism: int = Field(default=1, ge=1, le=32, strict=True)

    @model_validator(mode="after")
    def cost_units(self):
        if self.max_cost is not None and self.currency is None:
            raise ValueError("A monetary budget requires an explicit currency")
        return self


class ResearchScopeRevision(ContractModel):
    revision: int = Field(ge=1, strict=True)
    question: str = Field(min_length=1, max_length=10_000)
    boundaries: str = Field(default="", max_length=20_000)
    start_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    end_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    inclusion: list[ShortText] = Field(default_factory=list, max_length=200)
    exclusion: list[ShortText] = Field(default_factory=list, max_length=200)
    seed_paper_ids: list[ShortText] = Field(default_factory=list, max_length=1000)
    objectives: list[ShortText] = Field(default_factory=list, max_length=200)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    created_at: datetime | None = None
    created_by: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value):
        if not value.strip():
            raise ValueError("Research question cannot be blank")
        return value

    @field_validator("created_at")
    @classmethod
    def creation_timezone(cls, value):
        return _aware_timestamp(value)

    @model_validator(mode="after")
    def ordered_scope(self):
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise ValueError("Research time range is reversed")
        if len(set(self.seed_paper_ids)) != len(self.seed_paper_ids):
            raise ValueError("Research seed Paper IDs must be unique")
        return self


class ResearchScope(ContractModel):
    schema_version: Literal[1] = 1
    id: str | None = Field(default=None, min_length=1, max_length=200)
    current_revision: int = Field(default=0, ge=0, strict=True)
    revisions: list[ResearchScopeRevision] = Field(default_factory=list)

    @model_validator(mode="after")
    def revision_lineage(self):
        if [item.revision for item in self.revisions] != list(range(1, len(self.revisions) + 1)):
            raise ValueError("Scope revisions must be contiguous and ordered from 1")
        if self.current_revision != len(self.revisions):
            raise ValueError("Current scope revision must identify the latest retained revision")
        return self


def append_scope_revision(
    scope: ResearchScope | Mapping,
    revision: ResearchScopeRevision | Mapping,
    *,
    expected_revision: int,
) -> ResearchScope:
    """Return a new semantic scope revision without mutating existing snapshots."""
    current = ResearchScope.model_validate(scope)
    addition = ResearchScopeRevision.model_validate(revision)
    if type(expected_revision) is not int or expected_revision != current.current_revision:
        raise ValueError("Research scope revision conflict")
    if addition.revision != current.current_revision + 1:
        raise ValueError("The new scope revision must immediately follow the current one")
    value = current.model_dump(mode="json")
    value["revisions"].append(addition.model_dump(mode="json"))
    value["current_revision"] = addition.revision
    return ResearchScope.model_validate(value)
