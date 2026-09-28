"""Bounded Crossref metadata retrieval and pure, persist-before-I/O budget rules.

No Paper/world writes, full-text downloads, API keys, or paid endpoints occur here.
Reuse one CrossrefClient per host to share its serial public-pool rate limiter.
The host MUST atomically persist reserve_search and every consume_attempt result
before network I/O, and reconcile/cache duplicate request IDs rather than resend.
These pure helpers alone are not a concurrent or durable reservation service.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from oaw_library.contracts import PaperMetadata, ResearchScopeRevision, normalize_doi, normalize_source_url, paper_identity_keys


API_ROOT = "https://api.crossref.org/works"
MAX_ATTEMPTS = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RATE_HEADERS = ("x-rate-limit-limit", "x-rate-limit-interval", "x-rate-limit-type", "x-concurrency-limit", "retry-after")
Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must have a timezone")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequestBase(FrozenModel):
    scope_id: Identifier
    scope_revision: int = Field(ge=1, strict=True)
    request_id: Identifier


class CrossrefSearchRequest(RequestBase):
    query: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]
    rows: int = Field(default=5, ge=1, le=20, strict=True)
    cursor: str = Field(default="*", min_length=1, max_length=8192)
    from_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    until_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    record_type: Literal["journal-article", "proceedings-article", "posted-content"] | None = "journal-article"

    @model_validator(mode="after")
    def year_order(self):
        if self.from_year is not None and self.until_year is not None and self.from_year > self.until_year:
            raise ValueError("Search year range is reversed")
        return self


class CrossrefResolveRequest(RequestBase):
    doi: str

    @field_validator("doi", mode="before")
    @classmethod
    def canonical_doi(cls, value):
        normalized = normalize_doi(value)
        if not normalized:
            raise ValueError("DOI is required")
        return normalized


SearchRequest = CrossrefSearchRequest | CrossrefResolveRequest


def request_fingerprint(request: SearchRequest) -> str:
    # Preserve replay of legacy request IDs whose payload predates the default
    # article filter. Explicit alternative types are distinct requests.
    excluded = {"record_type"} if isinstance(request, CrossrefSearchRequest) and request.record_type == "journal-article" else set()
    payload = {"operation": "search" if isinstance(request, CrossrefSearchRequest) else "resolve", **request.model_dump(mode="json", exclude=excluded)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class BudgetReservation(FrozenModel):
    request_id: Identifier
    fingerprint: Digest
    candidate_slots: int = Field(ge=1, le=20, strict=True)
    attempts: int = Field(default=0, ge=0, le=MAX_ATTEMPTS, strict=True)


class SearchBudgetLedger(FrozenModel):
    scope_id: Identifier
    scope_revision: int = Field(ge=1, strict=True)
    max_searches: int = Field(ge=0, le=1_000_000, strict=True)
    max_candidate_slots: int = Field(ge=0, le=1_000_000, strict=True)
    started_at: datetime
    deadline: datetime | None = None
    from_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    until_year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    reservations: tuple[BudgetReservation, ...] = ()

    @field_validator("started_at", "deadline")
    @classmethod
    def timestamps(cls, value):
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def bounded(self):
        if len({r.request_id for r in self.reservations}) != len(self.reservations):
            raise ValueError("Duplicate budget reservation ID")
        if self.searches_used > self.max_searches or self.candidate_slots_used > self.max_candidate_slots:
            raise ValueError("Ledger exceeds its budget")
        if self.deadline is not None and self.deadline < self.started_at:
            raise ValueError("Budget deadline precedes its start")
        if self.from_year is not None and self.until_year is not None and self.from_year > self.until_year:
            raise ValueError("Budget year range is reversed")
        return self

    @property
    def searches_used(self) -> int:
        return len(self.reservations)

    @property
    def candidate_slots_used(self) -> int:
        return sum(r.candidate_slots for r in self.reservations)

    @property
    def attempts_used(self) -> int:
        return sum(r.attempts for r in self.reservations)


class SearchBudgetExceeded(ValueError):
    pass


def new_search_budget(scope_id: str, revision: ResearchScopeRevision, *, now: datetime | None = None) -> SearchBudgetLedger:
    """Count each search OR DOI resolution; reserve requested rows conservatively.

    Unspecified limits become 20 operations / 100 candidate slots. Crossref has
    zero API fees; this ledger neither authorizes nor budgets other providers,
    model inference, downloads or scientific execution. Failed/cancelled runs
    keep reservations. A host may impose stricter cumulative cross-revision caps.
    """
    revision = ResearchScopeRevision.model_validate(revision)
    started = _aware(now or utcnow())
    budget = revision.budget
    return SearchBudgetLedger(scope_id=scope_id, scope_revision=revision.revision,
        max_searches=budget.max_searches if budget.max_searches is not None else 20,
        max_candidate_slots=budget.max_papers if budget.max_papers is not None else 100,
        started_at=started,
        deadline=started + timedelta(seconds=budget.max_duration_seconds) if budget.max_duration_seconds is not None else None,
        from_year=revision.start_year, until_year=revision.end_year)


def _check_budget_scope(ledger: SearchBudgetLedger, request: SearchRequest, now: datetime) -> None:
    _aware(now)
    if (ledger.scope_id, ledger.scope_revision) != (request.scope_id, request.scope_revision):
        raise SearchBudgetExceeded("Request does not match the reserved scope revision")
    if now < ledger.started_at:
        raise SearchBudgetExceeded("Clock precedes budget start")
    if ledger.deadline is not None and now >= ledger.deadline:
        raise SearchBudgetExceeded("Scope search duration budget exhausted")
    if isinstance(request, CrossrefSearchRequest):
        if ledger.from_year is not None and (request.from_year is None or request.from_year < ledger.from_year):
            raise SearchBudgetExceeded("Search omits or widens the scope's lower year boundary")
        if ledger.until_year is not None and (request.until_year is None or request.until_year > ledger.until_year):
            raise SearchBudgetExceeded("Search omits or widens the scope's upper year boundary")


def reserve_search(ledger: SearchBudgetLedger, request: SearchRequest, *, now: datetime | None = None) -> tuple[SearchBudgetLedger, bool]:
    """Return (new ledger, replay). On replay host MUST use/reconcile saved run."""
    _check_budget_scope(ledger, request, now or utcnow())
    fingerprint = request_fingerprint(request)
    for prior in ledger.reservations:
        if prior.request_id == request.request_id:
            if prior.fingerprint != fingerprint:
                raise SearchBudgetExceeded("Idempotency ID reused with different search parameters")
            return ledger, True
    slots = request.rows if isinstance(request, CrossrefSearchRequest) else 1
    if ledger.searches_used >= ledger.max_searches:
        raise SearchBudgetExceeded("Search count budget exhausted")
    if ledger.candidate_slots_used + slots > ledger.max_candidate_slots:
        raise SearchBudgetExceeded("Candidate slot budget exhausted")
    reservation = BudgetReservation(request_id=request.request_id, fingerprint=fingerprint, candidate_slots=slots)
    return ledger.model_copy(update={"reservations": (*ledger.reservations, reservation)}), False


def consume_attempt(ledger: SearchBudgetLedger, request: SearchRequest, attempt: int, *, now: datetime | None = None) -> SearchBudgetLedger:
    """Reserve each outbound attempt before I/O, including the bounded 429 retry.

    Exact attempt sequencing prevents a replay from dispatching attempt one again.
    Persist the returned ledger atomically. No network, refund, or state writes.
    """
    _check_budget_scope(ledger, request, now or utcnow())
    for index, reservation in enumerate(ledger.reservations):
        if reservation.request_id != request.request_id:
            continue
        if reservation.fingerprint != request_fingerprint(request):
            raise SearchBudgetExceeded("Attempt does not match the reserved request")
        if type(attempt) is not int or attempt != reservation.attempts + 1 or attempt > MAX_ATTEMPTS:
            raise SearchBudgetExceeded("Attempt was already consumed or retry budget is exhausted")
        updated = reservation.model_copy(update={"attempts": attempt})
        return ledger.model_copy(update={"reservations": (*ledger.reservations[:index], updated, *ledger.reservations[index + 1:])})
    raise SearchBudgetExceeded("Search must be reserved before an HTTP attempt")


class HttpAttempt(FrozenModel):
    number: int
    requested_at: datetime
    responded_at: datetime | None = None
    status_code: int | None = None
    rate_headers: dict[str, str] = Field(default_factory=dict)
    response_sha256: str | None = None
    error: str | None = None


class SearchRun(FrozenModel):
    provider: Literal["crossref"] = "crossref"
    operation: Literal["search", "resolve"]
    request_id: str
    scope_id: str
    scope_revision: int
    query: str | None = None
    doi: str | None = None
    requested_rows: int
    request_url: str
    request_parameters: dict[str, str | int]
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "succeeded", "not_in_crossref", "failed", "cancelled"] = "running"
    attempts: tuple[HttpAttempt, ...] = ()
    total_results: int | None = None
    raw_item_count: int = 0
    candidate_count: int = 0
    duplicate_count: int = 0
    rejected_records: tuple[str, ...] = ()
    next_cursor: str | None = None
    error_code: str | None = None


class MetadataProvenance(FrozenModel):
    provider: Literal["crossref"] = "crossref"
    source_kind: Literal["deposited_bibliographic_metadata"] = "deposited_bibliographic_metadata"
    record_url: str
    request_id: str
    scope_revision: int
    retrieved_at: datetime
    record_sha256: Digest
    field_sources: dict[str, str]
    absent_fields: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class FullTextLink(FrozenModel):
    url: str
    content_type: str | None = None
    content_version: str | None = None
    intended_application: str | None = None
    availability: Literal["not_checked"] = "not_checked"


class PaperCandidate(FrozenModel):
    metadata: PaperMetadata
    identity_keys: tuple[str, ...]
    provenance: MetadataProvenance
    abstract_status: Literal["present", "not_deposited", "unusable"]
    fulltext_status: Literal["links_deposited_access_unchecked", "no_links_deposited", "unusable_links_deposited"]
    fulltext_links: tuple[FullTextLink, ...] = ()
    license_urls: tuple[str, ...] = ()
    record_type: str | None = None
    component_of_dois: tuple[str, ...] = ()
    canonical_metadata: PaperMetadata | None = None
    canonical_provenance: MetadataProvenance | None = None
    canonical_record_type: str | None = None
    parent_resolution_status: str | None = None


class SearchResult(FrozenModel):
    run: SearchRun
    candidates: tuple[PaperCandidate, ...] = ()


class CrossrefError(RuntimeError):
    def __init__(self, code: str, run: SearchRun):
        super().__init__(f"Crossref metadata request failed: {code}")
        self.code = code
        self.run = run


class _PlainText(HTMLParser):
    blocks = {"p", "title", "sec", "br", "div", "li", "list", "abstract"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.split(":")[-1].lower()
        if tag in {"script", "style"}:
            self.ignored.append(tag)
        if tag in self.blocks and not self.ignored:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.split(":")[-1].lower()
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag in self.blocks:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def plain_metadata_text(value: str) -> str:
    """Extract inert HTML/JATS text; no rendering, external entities, or I/O."""
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _safe_url(value) -> str | None:
    try:
        return normalize_source_url(value) if isinstance(value, str) else None
    except ValueError:
        return None


def crossref_candidate(record: Mapping, request: SearchRequest, retrieved_at: datetime) -> PaperCandidate:
    """Map only deposited fields. Invalid/missing DOI records cannot be merged."""
    doi = normalize_doi(record.get("DOI"))
    if not doi:
        raise ValueError("Record has no usable DOI identity")
    record_url = f"{API_ROOT}/{quote(doi, safe='')}"
    fields = {"doi": "DOI"}
    absent: list[str] = []
    warnings: list[str] = []
    raw_title = record.get("title")
    title = plain_metadata_text(raw_title[0]) if isinstance(raw_title, list) and raw_title and isinstance(raw_title[0], str) else ""
    fields["title"] = "title[0]" if title else "absent"
    if not title:
        absent.append("title")
    authors = []
    if isinstance(record.get("author"), list):
        for author in record["author"]:
            if not isinstance(author, dict):
                warnings.append("Unusable author entry omitted")
                continue
            parts = [author.get(key) for key in ("given", "family") if isinstance(author.get(key), str)]
            name = plain_metadata_text(" ".join(parts) or (author.get("name") if isinstance(author.get("name"), str) else ""))
            if name:
                authors.append(name)
    fields["authors"] = "author" if authors else "absent"
    if not authors:
        absent.append("authors")
    year = None
    for date_field in ("published", "published-print", "published-online", "issued"):
        date = record.get(date_field)
        date_parts = date.get("date-parts") if isinstance(date, dict) else None
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            value = date_parts[0][0]
            if type(value) is int and 1000 <= value <= 9999:
                year, fields["year"] = value, f"{date_field}.date-parts[0][0]"
                break
    if year is None:
        absent.append("year")
        fields["year"] = "absent"
    abstract_raw = record.get("abstract")
    abstract = plain_metadata_text(abstract_raw) if isinstance(abstract_raw, str) else ""
    abstract_status = "present" if abstract else "unusable" if "abstract" in record else "not_deposited"
    fields["source_abstract"] = "abstract" if abstract else "absent"
    if not abstract:
        absent.append("source_abstract")
    source_url = _safe_url(record.get("URL"))
    fields["source_url"] = "URL" if source_url else "DOI resolver constructed from deposited DOI"
    if not source_url:
        source_url = f"https://doi.org/{quote(doi, safe='/')}"
        warnings.append("No usable deposited URL; source_url is an unfetched DOI resolver")
    links = []
    raw_links = record.get("link")
    if isinstance(raw_links, list):
        for link in raw_links[:100]:
            if not isinstance(link, dict) or not (url := _safe_url(link.get("URL"))):
                warnings.append("Unsafe or unusable full-text link omitted")
                continue
            links.append(FullTextLink(url=url,
                content_type=link.get("content-type") if isinstance(link.get("content-type"), str) else None,
                content_version=link.get("content-version") if isinstance(link.get("content-version"), str) else None,
                intended_application=link.get("intended-application") if isinstance(link.get("intended-application"), str) else None))
        if len(raw_links) > 100:
            warnings.append("Full-text links bounded to first 100 deposits")
    licenses = record.get("license")
    license_urls = tuple(dict.fromkeys(url for value in licenses[:100] if isinstance(value, dict) and (url := _safe_url(value.get("URL"))))) if isinstance(licenses, list) else ()
    parents = []
    relation = record.get("relation")
    raw_parents = relation.get("is-component-of", []) if isinstance(relation, dict) else []
    for item in raw_parents if isinstance(raw_parents, list) else []:
        if not isinstance(item, dict) or str(item.get("id-type", "doi")).lower() != "doi":
            continue
        try:
            parent = normalize_doi(item.get("id"))
        except (ValueError, TypeError):
            parent = None
        if parent and parent != doi and parent not in parents:
            parents.append(parent)
    metadata = PaperMetadata(title=title, authors=authors, year=year, doi=doi,
        source_abstract=abstract, abstract_source_url=record_url if abstract else None, source_url=source_url)
    return PaperCandidate(metadata=metadata, identity_keys=paper_identity_keys(metadata),
        provenance=MetadataProvenance(record_url=record_url, request_id=request.request_id,
            scope_revision=request.scope_revision, retrieved_at=_aware(retrieved_at),
            record_sha256=hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
            field_sources=fields, absent_fields=tuple(absent), warnings=tuple(warnings)),
        abstract_status=abstract_status,
        fulltext_status="links_deposited_access_unchecked" if links else "unusable_links_deposited" if raw_links else "no_links_deposited",
        fulltext_links=tuple(links), license_urls=license_urls,
        record_type=record.get("type") if isinstance(record.get("type"), str) else None,
        component_of_dois=tuple(parents))


BeforeAttempt = Callable[[SearchRequest, int], Awaitable[None]]


class CrossrefClient:
    """Public-only async adapter. One page, <=20 rows, <=2 HTTP attempts.

    Supplied MockTransport is for isolated tests. No supplied authenticated client
    or arbitrary base URL is accepted. Cancellation propagates CancelledError with
    .search_run attached for host audit; it does not silently refund reservations.
    """
    def __init__(self, *, mailto: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 total_timeout: float = 30.0, connect_timeout: float = 15.0, max_retry_wait: float = 5.0):
        if not math.isfinite(total_timeout) or not 0 < total_timeout <= 60:
            raise ValueError("Total timeout must be positive and at most 60 seconds")
        if not math.isfinite(max_retry_wait) or not 0 <= max_retry_wait <= 10:
            raise ValueError("Retry wait must be between zero and ten seconds")
        if not math.isfinite(connect_timeout) or not 0 < connect_timeout <= 30:
            raise ValueError("Connect timeout must be positive and at most 30 seconds")
        if mailto is not None and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", mailto):
            raise ValueError("mailto must be a real operator contact email or omitted")
        self._mailto = mailto
        self._transport = transport
        self._total_timeout = total_timeout
        self._connect_timeout = connect_timeout
        self._max_retry_wait = max_retry_wait
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._interval = 1.05  # Conservative public-list ceiling, including DOI lookups.

    async def search(self, request: CrossrefSearchRequest, *, before_attempt: BeforeAttempt | None = None) -> SearchResult:
        request = CrossrefSearchRequest.model_validate(request)
        params: dict[str, str | int] = {"query": request.query, "rows": request.rows,
            "cursor": request.cursor, "sort": "score", "order": "desc"}
        filters = []
        if request.record_type is not None:
            filters.append(f"type:{request.record_type}")
        if request.from_year is not None:
            filters.append(f"from-pub-date:{request.from_year}-01-01")
        if request.until_year is not None:
            filters.append(f"until-pub-date:{request.until_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        return await self._execute(request, API_ROOT, params, before_attempt)

    async def resolve(self, request: CrossrefResolveRequest, *, before_attempt: BeforeAttempt | None = None) -> SearchResult:
        request = CrossrefResolveRequest.model_validate(request)
        return await self._execute(request, f"{API_ROOT}/{quote(request.doi, safe='')}", {}, before_attempt)

    async def _wait_rate_slot(self):
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0.0, self._next_request_at - loop.time()))
        self._next_request_at = loop.time() + self._interval

    def _observe_rate(self, response: httpx.Response):
        try:
            limit = int(response.headers["x-rate-limit-limit"])
            interval = float(response.headers["x-rate-limit-interval"].removesuffix("s"))
            if limit > 0 and math.isfinite(interval) and interval > 0:
                self._interval = max(self._interval, interval / limit + 0.05)
        except (KeyError, ValueError):
            pass
        self._next_request_at = max(self._next_request_at, asyncio.get_running_loop().time() + self._interval)

    def _retry_delay(self, response: httpx.Response) -> float:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(raw) - utcnow()).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = self._interval * 2
            if not math.isfinite(delay):
                return self._max_retry_wait + 1
            return max(0.0, delay, self._interval * 2)
        return self._interval * 2

    async def _execute(self, request: SearchRequest, url: str, params: dict[str, str | int], before_attempt: BeforeAttempt | None) -> SearchResult:
        is_search = isinstance(request, CrossrefSearchRequest)
        run = SearchRun(operation="search" if is_search else "resolve", request_id=request.request_id,
            scope_id=request.scope_id, scope_revision=request.scope_revision,
            query=request.query if is_search else None, doi=None if is_search else request.doi,
            requested_rows=request.rows if is_search else 1, request_url=url,
            request_parameters=params, started_at=utcnow())
        attempts: list[HttpAttempt] = []

        def finish(status: str, **updates) -> SearchRun:
            return run.model_copy(update={"status": status, "completed_at": utcnow(), "attempts": tuple(attempts), **updates})

        def fail(code: str) -> CrossrefError:
            return CrossrefError(code, finish("failed", error_code=code))

        try:
            async with asyncio.timeout(self._total_timeout):
                async with self._lock:
                    actual_params = {**params, **({"mailto": self._mailto} if self._mailto else {})}
                    async with httpx.AsyncClient(transport=self._transport, follow_redirects=False,
                        timeout=httpx.Timeout(connect=self._connect_timeout, read=15, write=15, pool=5),
                        headers={"User-Agent": "OAW-Literature/0.1 (bounded metadata research)", "Accept": "application/json"}) as client:
                        for number in range(1, MAX_ATTEMPTS + 1):
                            await self._wait_rate_slot()
                            if before_attempt is not None:
                                try:
                                    await before_attempt(request, number)
                                except Exception as error:
                                    raise fail("budget_or_host_rejected") from error
                            attempts.append(HttpAttempt(number=number, requested_at=utcnow()))
                            async with client.stream("GET", url, params=actual_params) as response:
                                self._observe_rate(response)
                                attempts[-1] = attempts[-1].model_copy(update={"responded_at": utcnow(),
                                    "status_code": response.status_code,
                                    "rate_headers": {key: response.headers[key] for key in RATE_HEADERS if key in response.headers}})
                                if response.status_code == 429:
                                    delay = self._retry_delay(response)
                                    self._next_request_at = max(self._next_request_at, asyncio.get_running_loop().time() + delay)
                                    if number == MAX_ATTEMPTS or delay > self._max_retry_wait:
                                        raise fail("rate_limited")
                                    # Release the response before waiting; next attempt shares the limiter.
                                    continue
                                if response.status_code == 404 and not is_search:
                                    return SearchResult(run=finish("not_in_crossref", error_code="doi_not_in_crossref"))
                                if response.status_code != 200:
                                    raise fail(f"http_{response.status_code}")
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    body.extend(chunk)
                                    if len(body) > MAX_RESPONSE_BYTES:
                                        raise fail("response_too_large")
                                attempts[-1] = attempts[-1].model_copy(update={"response_sha256": hashlib.sha256(body).hexdigest()})
                            try:
                                payload = json.loads(body)
                                message = payload["message"]
                                if payload.get("status") != "ok" or not isinstance(message, dict):
                                    raise ValueError("Invalid envelope")
                                records = message["items"] if is_search else [message]
                                if not isinstance(records, list) or len(records) > run.requested_rows:
                                    raise ValueError("Unbounded or invalid item list")
                            except (ValueError, TypeError, KeyError) as error:
                                raise fail("invalid_response") from error
                            candidates, rejected, seen = [], [], set()
                            duplicates = 0
                            for index, record in enumerate(records):
                                try:
                                    if not isinstance(record, dict):
                                        raise ValueError("Not a metadata record")
                                    candidate = crossref_candidate(record, request, utcnow())
                                    if not is_search and candidate.metadata.doi != request.doi:
                                        raise ValueError("Resolved DOI does not match requested DOI")
                                except (ValueError, TypeError) as error:
                                    rejected.append(f"record[{index}]: {type(error).__name__}")
                                    continue
                                key = candidate.identity_keys[0]
                                if key in seen:
                                    duplicates += 1
                                    continue
                                seen.add(key)
                                candidates.append(candidate)
                            total = message.get("total-results") if is_search else 1
                            total = total if type(total) is int and total >= 0 else None
                            cursor = message.get("next-cursor") if is_search else None
                            # A short page is terminal even if upstream leaves a cursor present.
                            if len(records) < run.requested_rows or not isinstance(cursor, str) or not 0 < len(cursor) <= 8192:
                                cursor = None
                            return SearchResult(run=finish("succeeded", raw_item_count=len(records),
                                candidate_count=len(candidates), duplicate_count=duplicates, rejected_records=tuple(rejected),
                                total_results=total, next_cursor=cursor), candidates=tuple(candidates))
            raise fail("attempts_exhausted")
        except asyncio.CancelledError as error:
            error.search_run = finish("cancelled", error_code="cancelled")
            raise
        except (TimeoutError, httpx.TimeoutException) as error:
            if attempts and attempts[-1].responded_at is None:
                attempts[-1] = attempts[-1].model_copy(update={"error": "timeout"})
            raise fail("timeout") from error
        except httpx.HTTPError as error:
            if attempts:
                attempts[-1] = attempts[-1].model_copy(update={"error": type(error).__name__})
            raise fail("network_error") from error
