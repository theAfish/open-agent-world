"""search_store.py in isolation: chunking and FTS5 query behavior, no host, no MKB."""
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from oaw_knowledge_base import search_store  # noqa: E402

DOCUMENT = """# Sintering of Si3N4

## Method
The Si3N4 powder was sintered at 1750 C for 2 hours under flowing nitrogen, using
Y2O3 and Al2O3 as sintering aids.

## Results
Sample A1 reached 96% of the theoretical density and showed the finest grain
structure, attributed to the combined liquid-phase sintering effect of the two
additives.
"""


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_chunks_carry_the_heading_they_fall_under():
    chunks = list(search_store.chunk_markdown(DOCUMENT))
    assert [heading for _, heading, _ in chunks] == [
        "Sintering of Si3N4", "Sintering of Si3N4 > Method", "Sintering of Si3N4 > Results"]
    assert "1750 C" in chunks[1][2]
    assert "96%" in chunks[2][2]


def test_chunk_indices_are_sequential_across_sections():
    chunks = list(search_store.chunk_markdown(DOCUMENT))
    assert [index for index, _, _ in chunks] == list(range(len(chunks)))


def test_search_finds_a_term_and_reports_its_heading_and_source(engine):
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text=DOCUMENT)
    results = search_store.search(engine, "sintering aids")
    assert results
    assert results[0]["source_id"] == "s1"
    assert results[0]["group_id"] == "g1"
    assert "Method" in results[0]["heading_path"]


def test_search_is_scoped_to_one_group_when_asked(engine):
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text=DOCUMENT)
    search_store.index_record(engine, record_id="r2", source_id="s2", group_id="g2",
                              text=DOCUMENT)
    scoped = search_store.search(engine, "sintering", group_id="g2")
    assert scoped and all(row["group_id"] == "g2" for row in scoped)


def test_reindexing_a_record_replaces_its_old_chunks(engine):
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text=DOCUMENT)
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text="# Only one line now\n\nNothing else survives.")
    results = search_store.search(engine, "sintering")
    assert not results
    results = search_store.search(engine, "survives")
    assert results and results[0]["record_id"] == "r1"


def test_search_handles_punctuation_and_empty_queries_without_raising(engine):
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text=DOCUMENT)
    assert search_store.search(engine, 'sintering OR "quoted": weird -syntax?') != []
    assert search_store.search(engine, "") == []
    assert search_store.search(engine, "   ") == []


def test_search_result_count_is_bounded_by_limit(engine):
    search_store.index_record(engine, record_id="r1", source_id="s1", group_id="g1",
                              text=DOCUMENT)
    results = search_store.search(engine, "sintering", limit=1)
    assert len(results) == 1
