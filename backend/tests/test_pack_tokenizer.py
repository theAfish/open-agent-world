"""Portable Pack wheels must preserve offline host token accounting."""
import hashlib
from importlib import resources

import pytest

from backend.agents.context import _local_tokenizer


@pytest.mark.parametrize('line_ending', [b'\n', b'\r\n'])
def test_bundled_tokenizer_accepts_platform_line_endings_without_rewriting(tmp_path, monkeypatch, line_ending):
    name = hashlib.sha1(b'https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken').hexdigest()
    source = resources.files('litellm.litellm_core_utils.tokenizers').joinpath(name).read_bytes()
    target = tmp_path / name
    content = source.replace(b'\r\n', b'\n').replace(b'\n', line_ending)
    target.write_bytes(content)
    monkeypatch.setattr(resources, 'files', lambda package: tmp_path)
    _local_tokenizer.cache_clear()
    try:
        assert _local_tokenizer().encode_ordinary('Hello world!') == [9906, 1917, 0]
        assert target.read_bytes() == content
    finally:
        _local_tokenizer.cache_clear()


def test_bundled_tokenizer_rejects_changed_content(tmp_path, monkeypatch):
    name = hashlib.sha1(b'https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken').hexdigest()
    (tmp_path / name).write_bytes(b'YQ== 0\r\n')
    monkeypatch.setattr(resources, 'files', lambda package: tmp_path)
    _local_tokenizer.cache_clear()
    try:
        with pytest.raises(ValueError, match='checksum'):
            _local_tokenizer()
    finally:
        _local_tokenizer.cache_clear()
