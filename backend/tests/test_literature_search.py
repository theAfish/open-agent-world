"""Isolated Crossref HTTP fixtures and durable-host budget boundary contracts."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/literature/src"))
sys.path.insert(0, str(ROOT / "plugins/library/src"))
from oaw_library.contracts import ResearchScopeRevision  # noqa: E402
from oaw_literature.search import (  # noqa: E402
    API_ROOT, MAX_RESPONSE_BYTES, CrossrefClient, CrossrefError, CrossrefResolveRequest,
    CrossrefSearchRequest, SearchBudgetExceeded, SearchBudgetLedger, consume_attempt,
    crossref_candidate, new_search_budget, plain_metadata_text, reserve_search,
)

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def search_request(**updates):
    return CrossrefSearchRequest(**{"scope_id": "scope-test", "scope_revision": 1,
        "request_id": "request-1", "query": "solid electrolyte ion transport", **updates})


def resolve_request(doi="10.1234/TEST", **updates):
    return CrossrefResolveRequest(**{"scope_id": "scope-test", "scope_revision": 1,
        "request_id": "resolve-1", "doi": doi, **updates})


def scope(**budget):
    return ResearchScopeRevision(revision=1, question="Public fixture topic", budget=budget)


def record(**updates):
    return {"DOI": "10.1234/TEST", "title": ["Solid <i>electrolyte</i> transport"],
        "author": [{"given": "Alice", "family": "Example"}, {"name": "Fixture Consortium"}],
        "published": {"date-parts": [[2024, 2]]}, "created": {"date-parts": [[2026]]},
        "URL": "https://doi.org/10.1234/TEST", **updates}


def response(items, **updates):
    return {"status": "ok", "message": {"total-results": 77, "items": items, **updates}}


def test_metadata_uses_only_deposits_strips_jats_and_does_not_claim_access():
    candidate = crossref_candidate(record(abstract="<jats:abstract><jats:p>Li<sub>2</sub>O &amp; Cl.</jats:p><jats:p>Second paragraph.<script>alert(1)</script></jats:p></jats:abstract>",
        link=[{"URL": "https://publisher.example/paper.pdf", "content-type": "application/pdf", "intended-application": "text-mining"},
              {"URL": "javascript:alert(1)"}],
        license=[{"URL": "https://creativecommons.org/licenses/by/4.0/"}]), search_request(), NOW)
    assert candidate.identity_keys == ("doi:10.1234/test",)
    assert candidate.metadata.title == "Solid electrolyte transport"
    assert candidate.metadata.authors == ["Alice Example", "Fixture Consortium"]
    assert candidate.metadata.year == 2024
    assert candidate.metadata.source_abstract == "Li2O & Cl. Second paragraph."
    assert candidate.metadata.agent_abstract == ""
    assert candidate.abstract_status == "present"
    assert candidate.provenance.field_sources["year"] == "published.date-parts[0][0]"
    assert candidate.metadata.abstract_source_url == candidate.provenance.record_url
    assert candidate.provenance.source_kind == "deposited_bibliographic_metadata"
    assert len(candidate.provenance.record_sha256) == 64
    assert len(candidate.fulltext_links) == 1
    assert candidate.fulltext_status == "links_deposited_access_unchecked"
    assert candidate.fulltext_links[0].availability == "not_checked"
    assert "Unsafe or unusable full-text link omitted" in candidate.provenance.warnings
    assert candidate.license_urls == ("https://creativecommons.org/licenses/by/4.0/",)


def test_missing_metadata_is_unknown_and_creation_year_is_not_publication():
    candidate = crossref_candidate({"DOI": "10.1234/empty", "created": {"date-parts": [[2026]]},
        "URL": "file:///private"}, search_request(), NOW)
    assert candidate.metadata.title == ""
    assert candidate.metadata.authors == [] and candidate.metadata.year is None
    assert candidate.metadata.source_abstract == ""
    assert candidate.metadata.abstract_source_url is None
    assert candidate.abstract_status == "not_deposited"
    assert candidate.fulltext_status == "no_links_deposited"
    assert candidate.metadata.source_url == "https://doi.org/10.1234/empty"
    assert set(candidate.provenance.absent_fields) == {"title", "authors", "year", "source_abstract"}
    empty = crossref_candidate(record(abstract="<jats:p></jats:p>"), search_request(), NOW)
    assert empty.abstract_status == "unusable"
    unsafe = crossref_candidate(record(link=[{"URL": "javascript:bad"}]), search_request(), NOW)
    assert unsafe.fulltext_status == "unusable_links_deposited" and not unsafe.fulltext_links
    with pytest.raises(ValueError):
        crossref_candidate({"title": ["No identity"]}, search_request(), NOW)
    # HTMLParser does not resolve external XML entities or execute script content.
    assert plain_metadata_text('<!DOCTYPE p SYSTEM "https://private.example/secret"><p>A<br>B</p><style>secret</style>') == "A B"


@pytest.mark.parametrize("updates", [{"rows": 0}, {"rows": 21}, {"rows": True}, {"query": "   "},
    {"scope_revision": 0}, {"query": "x" * 1001}, {"from_year": 2026, "until_year": 2020}, {"api_key": "forbidden"}])
def test_request_has_hard_bounds(updates):
    with pytest.raises(ValidationError):
        search_request(**updates)


@pytest.mark.asyncio
async def test_one_page_preserves_provenance_deduplicates_and_encodes_cursor():
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.url.host == "api.crossref.org"
        assert request.url.params["query"] == "solid electrolyte ion transport"
        assert request.url.params["sort"] == "score" and request.url.params["order"] == "desc"
        assert request.url.params["cursor"] == "abc+def/=="
        assert request.url.params["filter"] == "type:journal-article,from-pub-date:2020-01-01,until-pub-date:2025-12-31"
        assert "authorization" not in request.headers
        assert "mailto" not in request.url.params
        return httpx.Response(200, json=response([record(), record(), {"title": ["Bad"]}, record(DOI="10.1234/other")],
            **{"next-cursor": "not-followed"}), headers={"x-rate-limit-limit": "1", "x-rate-limit-interval": "1s"})

    result = await CrossrefClient(transport=httpx.MockTransport(handler)).search(search_request(rows=4,
        cursor="abc+def/==", from_year=2020, until_year=2025))
    assert len(calls) == 1 and len(result.candidates) == 2
    assert result.run.raw_item_count == 4 and result.run.candidate_count == 2
    assert result.run.duplicate_count == 1 and len(result.run.rejected_records) == 1
    assert result.run.query == "solid electrolyte ion transport"
    assert result.run.scope_revision == 1 and result.run.provider == "crossref"
    assert result.run.total_results == 77 and result.run.next_cursor == "not-followed"
    assert result.run.started_at <= result.run.attempts[0].requested_at <= result.run.attempts[0].responded_at <= result.run.completed_at
    assert len(result.run.attempts[0].response_sha256) == 64
    assert result.run.request_parameters["rows"] == 4


@pytest.mark.asyncio
async def test_short_page_stops_cursor_and_doi_resolve_escapes_identity():
    async def handler(request):
        if request.url.path == "/works":
            return httpx.Response(200, json=response([], **{"next-cursor": "spurious"}))
        assert unquote(request.url.raw_path.decode()) == "/works/10.1234/a?b#c"
        assert request.url.query == b""  # DOI punctuation stays in the path, never query/fragment.
        return httpx.Response(200, json={"status": "ok", "message": record(DOI="10.1234/a?b#c")})

    result = await CrossrefClient(transport=httpx.MockTransport(handler)).search(search_request())
    assert result.run.next_cursor is None and result.run.total_results == 77
    result = await CrossrefClient(transport=httpx.MockTransport(handler)).resolve(resolve_request("10.1234/A?B#C"))
    assert result.run.operation == "resolve" and result.run.doi == "10.1234/a?b#c"
    assert result.candidates[0].metadata.doi == "10.1234/a?b#c"
    assert result.candidates[0].identity_keys == ("doi:10.1234/a?b#c",)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(403, "http_403"), (500, "http_500"), (302, "http_302")])
async def test_http_errors_do_not_follow_or_retry(status, code):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://private.example/no"})

    with pytest.raises(CrossrefError) as captured:
        await CrossrefClient(transport=httpx.MockTransport(handler)).search(search_request())
    assert len(calls) == 1 and captured.value.code == code
    assert captured.value.run.status == "failed" and captured.value.run.attempts[0].status_code == status


@pytest.mark.asyncio
async def test_missing_crossref_doi_is_not_global_nonexistence():
    result = await CrossrefClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))).resolve(resolve_request())
    assert result.run.status == "not_in_crossref" and result.candidates == ()
    assert result.run.error_code == "doi_not_in_crossref"


@pytest.mark.asyncio
async def test_429_retry_is_bounded_and_every_attempt_passes_budget_hook(monkeypatch):
    seen, reservations = [], []
    client = CrossrefClient(transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(429, headers={"retry-after": "0"})))

    async def skip_wait():
        pass

    async def before_attempt(request, attempt):
        reservations.append((request.request_id, attempt))

    monkeypatch.setattr(client, "_wait_rate_slot", skip_wait)
    with pytest.raises(CrossrefError) as captured:
        await client.search(search_request(), before_attempt=before_attempt)
    assert captured.value.code == "rate_limited"
    assert len(seen) == len(captured.value.run.attempts) == 2
    assert reservations == [("request-1", 1), ("request-1", 2)]
    assert client._next_request_at > asyncio.get_running_loop().time()


@pytest.mark.asyncio
async def test_long_retry_after_stops_without_premature_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "120"})

    with pytest.raises(CrossrefError) as captured:
        await CrossrefClient(transport=httpx.MockTransport(handler)).search(search_request())
    assert captured.value.code == "rate_limited" and len(calls) == 1


@pytest.mark.asyncio
async def test_live_rate_headers_lower_client_pace_but_cannot_raise_public_ceiling():
    client = CrossrefClient()
    client._observe_rate(httpx.Response(200, headers={"x-rate-limit-limit": "10", "x-rate-limit-interval": "1s"}))
    assert client._interval == 1.05
    client._observe_rate(httpx.Response(200, headers={"x-rate-limit-limit": "1", "x-rate-limit-interval": "3s"}))
    assert client._interval == 3.05


@pytest.mark.asyncio
async def test_timeout_and_cancellation_retain_auditable_attempts():
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()

    transport = httpx.MockTransport(handler)
    with pytest.raises(CrossrefError) as captured:
        await CrossrefClient(transport=transport, total_timeout=0.02).search(search_request())
    assert captured.value.code == "timeout" and len(captured.value.run.attempts) == 1
    entered.clear()
    task = asyncio.create_task(CrossrefClient(transport=httpx.MockTransport(handler)).search(search_request()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert cancelled.value.search_run.status == "cancelled"
    assert len(cancelled.value.search_run.attempts) == 1


@pytest.mark.asyncio
async def test_host_budget_rejection_prevents_network():
    calls = []

    async def denied(request, attempt):
        raise SearchBudgetExceeded("Denied")

    with pytest.raises(CrossrefError) as captured:
        await CrossrefClient(transport=httpx.MockTransport(lambda r: calls.append(r))).search(search_request(), before_attempt=denied)
    assert captured.value.code == "budget_or_host_rejected"
    assert not calls and not captured.value.run.attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("body,code", [(b"not JSON", "invalid_response"),
    (json.dumps(response([record()] * 6)).encode(), "invalid_response"),
    (b"x" * (MAX_RESPONSE_BYTES + 1), "response_too_large")], ids=["bad-json", "too-many-items", "too-many-bytes"])
async def test_malformed_or_unbounded_response_is_not_accepted(body, code):
    with pytest.raises(CrossrefError) as captured:
        await CrossrefClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))).search(search_request())
    assert captured.value.code == code and not captured.value.run.candidate_count


def test_budget_reservation_is_pure_hard_bounded_and_idempotent():
    ledger = new_search_budget("scope-test", scope(max_searches=2, max_papers=6), now=NOW)
    request = search_request()
    first, replay = reserve_search(ledger, request, now=NOW)
    assert not replay and ledger.searches_used == 0 and first.searches_used == 1
    assert first.candidate_slots_used == 5
    duplicate, replay = reserve_search(first, request, now=NOW)
    assert replay and duplicate is first
    with pytest.raises(SearchBudgetExceeded, match="different search parameters"):
        reserve_search(first, search_request(query="changed"), now=NOW)
    with pytest.raises(SearchBudgetExceeded, match="Candidate slot"):
        reserve_search(first, search_request(request_id="request-2", rows=2), now=NOW)
    final, _ = reserve_search(first, resolve_request(), now=NOW)
    assert final.searches_used == 2 and final.candidate_slots_used == 6
    with pytest.raises(SearchBudgetExceeded, match="Search count"):
        reserve_search(final, resolve_request(request_id="resolve-2"), now=NOW)
    assert SearchBudgetLedger.model_validate_json(final.model_dump_json()) == final


def test_budget_attempts_are_charged_before_io_with_no_replay_or_third_try():
    ledger = new_search_budget("scope-test", scope(), now=NOW)
    request = search_request()
    with pytest.raises(SearchBudgetExceeded, match="reserved"):
        consume_attempt(ledger, request, 1, now=NOW)
    ledger, _ = reserve_search(ledger, request, now=NOW)
    ledger = consume_attempt(ledger, request, 1, now=NOW)
    assert ledger.attempts_used == 1 and ledger.candidate_slots_used == 5
    with pytest.raises(SearchBudgetExceeded):
        consume_attempt(ledger, request, 1, now=NOW)
    with pytest.raises(SearchBudgetExceeded):
        consume_attempt(ledger, search_request(query="altered"), 2, now=NOW)
    ledger = consume_attempt(ledger, request, 2, now=NOW)
    with pytest.raises(SearchBudgetExceeded):
        consume_attempt(ledger, request, 3, now=NOW)
    assert ledger.attempts_used == 2


def test_budget_respects_scope_revision_year_limits_and_deadline():
    revision = ResearchScopeRevision(revision=1, question="Public", start_year=2020, end_year=2025,
        budget={"max_duration_seconds": 10})
    ledger = new_search_budget("scope-test", revision, now=NOW)
    with pytest.raises(SearchBudgetExceeded, match="lower year"):
        reserve_search(ledger, search_request(), now=NOW)
    with pytest.raises(SearchBudgetExceeded, match="upper year"):
        reserve_search(ledger, search_request(from_year=2020), now=NOW)
    request = search_request(from_year=2021, until_year=2025)
    ledger, _ = reserve_search(ledger, request, now=NOW)
    with pytest.raises(SearchBudgetExceeded, match="scope revision"):
        reserve_search(ledger, search_request(scope_revision=2), now=NOW)
    with pytest.raises(SearchBudgetExceeded, match="duration"):
        consume_attempt(ledger, request, 1, now=NOW + timedelta(seconds=10))
    with pytest.raises(SearchBudgetExceeded, match="Clock"):
        reserve_search(ledger, request, now=NOW - timedelta(seconds=1))


def test_unspecified_limits_get_defaults_and_zero_limits_block():
    default = new_search_budget("scope-test", scope(), now=NOW)
    assert default.max_searches == 20 and default.max_candidate_slots == 100
    for budget in ({"max_searches": 0}, {"max_papers": 0}, {"max_duration_seconds": 0}):
        ledger = new_search_budget("scope-test", scope(**budget), now=NOW)
        with pytest.raises(SearchBudgetExceeded):
            reserve_search(ledger, search_request(), now=NOW)


@pytest.mark.asyncio
async def test_real_budget_hook_charges_cancelled_io_and_replay_cannot_send_again():
    request = search_request()
    ledger, _ = reserve_search(new_search_budget("scope-test", scope(max_searches=1, max_papers=5)), request)
    sent = asyncio.Event()

    async def handler(_):
        sent.set()
        await asyncio.Event().wait()

    async def persist_before_attempt(request, attempt):
        nonlocal ledger
        ledger = consume_attempt(ledger, request, attempt)

    client = CrossrefClient(transport=httpx.MockTransport(handler))
    task = asyncio.create_task(client.search(request, before_attempt=persist_before_attempt))
    await asyncio.wait_for(sent.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.attempts_used == 1 and ledger.searches_used == 1 and ledger.candidate_slots_used == 5
    _, replay = reserve_search(ledger, request)
    assert replay
    with pytest.raises(SearchBudgetExceeded):
        consume_attempt(ledger, request, 1)
