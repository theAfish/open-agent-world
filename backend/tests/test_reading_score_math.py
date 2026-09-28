"""Scorer contracts run without optional ML dependencies or a model download."""
import importlib.util
import math
from pathlib import Path
import threading

import pytest

_path = Path(__file__).resolve().parents[2] / "plugins/library/src/oaw_library/scoring_worker.py"
_spec = importlib.util.spec_from_file_location("reading_scoring_worker", _path)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


def test_logprob_is_stable_natural_log_and_converts_to_bits():
    actual = worker.logprob_from_logits([1000 + math.log(.25), 1000 + math.log(.75)], 0)
    assert actual == pytest.approx(math.log(.25), abs=1e-12)
    assert -actual / math.log(2) == pytest.approx(2)
    assert worker.logprob_from_logits([0, 0, 0, 0], 3) == pytest.approx(-math.log(4))


@pytest.mark.parametrize("values,target", [([], 0), ([0], 1), ([float("nan")], 0), ([float("inf")], 0)])
def test_invalid_probabilities_are_rejected(values, target):
    with pytest.raises(ValueError):
        worker.logprob_from_logits(values, target)


@pytest.mark.parametrize("count,context,stride", [(0, 256, 128), (1, 256, 128), (2, 2, 1), (1025, 256, 128), (513, 512, 511)])
def test_sliding_windows_score_each_source_target_once_with_preceding_context(count, context, stride):
    seen = []
    for begin, first, end in worker.token_windows(count, context, stride):
        assert 0 <= begin < first < end <= count
        assert end - begin <= context
        for index in range(first, end):
            assert 0 <= index - begin - 1 < end - begin - 1
            seen.append(index)
    assert seen == list(range(1, count))


def test_utf16_and_utf8_boundaries_preserve_chinese_emoji_and_combining_characters():
    text = "A锂🙂e\u0301\n"
    utf16, utf8 = worker.character_boundaries(text)
    assert utf16 == [0, 1, 2, 4, 5, 6, 7]
    assert utf8 == [0, 1, 4, 8, 9, 11, 12]
    assert text.encode("utf-8")[utf8[2]:utf8[3]].decode("utf-8") == "🙂"
    assert text.encode("utf-16-le")[2 * utf16[2]:2 * utf16[3]].decode("utf-16-le") == "🙂"


def test_invalid_windows_and_text_fail_before_loading_optional_model(tmp_path):
    scorer = worker.LocalScorer(tmp_path)
    with pytest.raises(ValueError):
        scorer.score("hello", 256, 256, threading.Event())
    with pytest.raises(ValueError):
        scorer.score(None, 256, 128, threading.Event())
    with pytest.raises(UnicodeEncodeError):
        scorer.score("\ud800", 256, 128, threading.Event())
    assert scorer.model is None


def test_cancellation_prevents_any_model_access(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(worker.ScoringCancelled):
        worker.LocalScorer(tmp_path).score("original source", 256, 128, cancelled)
