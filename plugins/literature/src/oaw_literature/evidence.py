"""Pure evidence contracts. Hosts own paper authorization and source resolution.

Location freshness never establishes scientific support. Reviewer records are
bound to a particular evidence payload and cannot survive a changed claim.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from oaw_library.contracts import AnchorStatus, Sha256, SourceAnchor


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Record timestamp must include a timezone")
    return value


class EvidenceSource(SourceAnchor):
    id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=100_000)
    quote_sha256: Sha256
    source_kind: Literal["paper_fulltext", "synthetic_fixture"] = "paper_fulltext"

    @model_validator(mode="after")
    def exact_locator(self):
        if not self.quote.strip() or not self.paper_id.strip() or not self.id.strip():
            raise ValueError("Evidence source requires a Paper, source ID and exact nonblank quote")
        if not self.rects and not self.text_ranges:
            raise ValueError("Evidence source requires page geometry or raw text-item ranges")
        return self


class ScientificReview(Model):
    id: str = Field(min_length=1, max_length=128)
    evidence_sha256: Sha256
    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_at: datetime
    decision: Literal["supports", "contradicts", "insufficient"]
    rationale: str = Field(min_length=1, max_length=20_000)

    @field_validator("reviewed_at")
    @classmethod
    def timezone(cls, value):
        return aware_timestamp(value)

    @field_validator("reviewer", "rationale")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Scientific review requires an identified reviewer and rationale")
        return value


class Evidence(Model):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1, strict=True)
    claim: str = Field(min_length=1, max_length=20_000)
    kind: Literal["author_statement", "research_fact", "agent_inference", "user_hypothesis"]
    relation: Literal["supports", "contradicts", "insufficient"] = "insufficient"
    conditions: list[str] = Field(default_factory=list, max_length=100)
    sources: list[EvidenceSource] = Field(min_length=1, max_length=100)
    extracted_by: str = Field(min_length=1, max_length=200)
    scientific_reviews: list[ScientificReview] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def readonly_review_projection(cls, value):
        if isinstance(value, dict) and "scientific_verification" in value:
            expected = "reviewed" if value.get("scientific_reviews") else "unreviewed"
            if value["scientific_verification"] != expected:
                raise ValueError("Scientific verification is derived from bound review records")
            value = {key: item for key, item in value.items() if key != "scientific_verification"}
        return value

    @model_validator(mode="after")
    def bounded_identity(self):
        if not self.claim.strip() or not self.extracted_by.strip():
            raise ValueError("Evidence needs a claim and an identified extractor")
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate evidence source IDs")
        if any(not condition.strip() or len(condition) > 5000 for condition in self.conditions):
            raise ValueError("Evidence conditions must be nonblank bounded text")
        if len({review.id for review in self.scientific_reviews}) != len(self.scientific_reviews):
            raise ValueError("Duplicate scientific review IDs")
        if any(review.evidence_sha256 != evidence_sha256(self) for review in self.scientific_reviews):
            raise ValueError("Scientific review belongs to a different evidence revision or claim")
        return self

    @computed_field
    @property
    def scientific_verification(self) -> Literal["unreviewed", "reviewed"]:
        # This is an auditable review state, not a probability of correctness.
        return "reviewed" if self.scientific_reviews else "unreviewed"


def evidence_sha256(evidence: Evidence | dict) -> str:
    value = evidence.model_dump(mode="json", exclude={"scientific_reviews", "scientific_verification"}) if isinstance(evidence, Evidence) else Evidence.model_validate(evidence).model_dump(
        mode="json", exclude={"scientific_reviews", "scientific_verification"})
    for source in value["sources"]:
        source.pop("status", None)
    return canonical_sha256(value)


SourceValidator = Callable[[EvidenceSource], AnchorStatus]


def validate_sources(sources: list[EvidenceSource], validator: SourceValidator, *,
    allow_synthetic: bool = False, require_current: bool = True) -> list[EvidenceSource]:
    """Apply a host-owned authority/quote validator, then record its freshness.

    The callback must resolve the authorized Paper and immutable bytes, check
    its page/quote/locator and return one of the three location statuses. This
    pure module does not fetch files, authorize papers, or grant scientific review.
    """
    checked = []
    for source in sources:
        source = EvidenceSource.model_validate(source)
        if source.source_kind == "synthetic_fixture" and not allow_synthetic:
            raise ValueError("Synthetic fixtures cannot be accepted as production research sources")
        status = validator(source)
        if not isinstance(status, str) or status not in {"current", "needs_relocation", "missing"}:
            raise ValueError("Source validator must return a location status, not a verification boolean")
        if require_current and status != "current":
            raise ValueError(f"Source {source.id} is {status}; relocate it before accepting new evidence")
        checked.append(source.model_copy(update={"status": status}))
    return checked


def validate_evidence_sources(evidence: Evidence | dict, validator: SourceValidator, *,
    allow_synthetic: bool = False, require_current: bool = True) -> Evidence:
    value = Evidence.model_validate(evidence)
    sources = validate_sources(value.sources, validator, allow_synthetic=allow_synthetic, require_current=require_current)
    return value.model_copy(update={"sources": sources}, deep=True)


def revise_evidence(evidence: Evidence | dict, changes: dict, *, expected_revision: int) -> Evidence:
    value = Evidence.model_validate(evidence)
    if type(expected_revision) is not int or expected_revision != value.revision:
        raise ValueError("Evidence revision conflict")
    if set(changes) - {"claim", "kind", "relation", "conditions", "sources", "extracted_by"}:
        raise ValueError("Only evidence content can be revised; reviews have a separate authority")
    result = value.model_dump(mode="json", exclude={"scientific_verification"})
    return Evidence.model_validate({**result, **changes, "revision": value.revision + 1, "scientific_reviews": []})


def record_scientific_review(evidence: Evidence | dict, review: ScientificReview | dict) -> Evidence:
    """Host must authorize the reviewer; current anchors alone cannot call this."""
    value = Evidence.model_validate(evidence)
    review = ScientificReview.model_validate(review)
    result = value.model_dump(mode="json", exclude={"scientific_verification"})
    existing = next((item for item in value.scientific_reviews if item.id == review.id), None)
    if existing:
        if existing != review:
            raise ValueError("Scientific review IDs are immutable; use a new review ID")
        return value.model_copy(deep=True)
    result["scientific_reviews"].append(review.model_dump(mode="json"))
    return Evidence.model_validate(result)
