"""markdown.py's tabular engines in isolation: CSV/TSV/XLSX -> a real markdown table."""
import io
import builtins
from importlib.machinery import ModuleSpec

import pytest

openpyxl = pytest.importorskip("openpyxl", reason="Install openpyxl to convert .xlsx files")

from oaw_knowledge_base.markdown import available_engines, to_markdown  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402


def test_csv_becomes_a_real_markdown_table_not_a_fenced_dump():
    data = b"Sample,Density\nA1,3.21\nA2,3.05\n"
    text, engine, metadata = to_markdown(data, "results.csv", "text/csv")
    assert engine == "tabular"
    assert metadata["engine"] == "tabular"
    assert "```" not in text
    assert "| Sample | Density |" in text
    assert "| A1 | 3.21 |" in text


def test_tsv_uses_tab_delimiter():
    data = b"Sample\tDensity\nA1\t3.21\n"
    text, engine, _ = to_markdown(data, "results.tsv", "text/tab-separated-values")
    assert engine == "tabular"
    assert "| Sample | Density |" in text
    assert "| A1 | 3.21 |" in text


def test_csv_cells_with_pipes_and_newlines_do_not_break_the_table():
    data = b'Note,Value\n"a | b","line1\nline2"\n'
    text, _, _ = to_markdown(data, "notes.csv", "text/csv")
    assert "a \\| b" in text
    assert "line1 line2" in text


def _xlsx_bytes(sheets):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_renders_every_sheet_under_its_own_heading():
    data = _xlsx_bytes({
        "Synthesis": [["Sample", "Temp"], ["A1", 1750]],
        "Results": [["Sample", "Density"], ["A1", 3.21]],
    })
    text, engine, _ = to_markdown(data, "experiment.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert engine == "tabular"
    assert "## Synthesis" in text
    assert "## Results" in text
    assert "| Sample | Temp |" in text
    assert "| A1 | 1750 |" in text
    assert "| A1 | 3.21 |" in text


def test_xlsx_detected_by_extension_without_a_matching_media_type():
    data = _xlsx_bytes({"Sheet": [["A"], [1]]})
    text, engine, _ = to_markdown(data, "plain.xlsx", "application/octet-stream")
    assert engine == "tabular"
    assert "| A |" in text


def test_a_corrupt_xlsx_reports_a_knowledge_error_not_a_crash():
    with pytest.raises(KnowledgeError):
        to_markdown(b"not a real workbook", "broken.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_large_xlsx_uses_bounded_private_file_handoff_instead_of_json_bytes(tmp_path, monkeypatch):
    import json
    import zipfile
    from pathlib import Path
    from oaw_knowledge_base import markdown

    path = tmp_path / 'large.xlsx'
    path.write_bytes(_xlsx_bytes({'Results': [['Sample', 'Value'], ['A', 3.2]]}))
    with zipfile.ZipFile(path, 'a') as archive, archive.open('padding.bin', 'w') as padding:
        for _ in range(132):
            padding.write(b'x' * markdown.FILE_CHUNK_BYTES)
    run = markdown.subprocess.run
    staged = []

    def inspect_handoff(*args, **kwargs):
        request = json.loads(kwargs['input'])
        assert 'data' not in request and len(kwargs['input']) < 16384
        staged.append(Path(request['path']))
        assert staged[-1].stat().st_size == path.stat().st_size
        return run(*args, **kwargs)

    monkeypatch.setattr(markdown.subprocess, 'run', inspect_handoff)
    with path.open('rb') as stream:
        text, _, _ = to_markdown(stream, path.name, '')
        assert not stream.closed
    assert '| A | 3.2 |' in text
    assert staged and all(not path.exists() for path in staged)


def test_tabular_row_budget_stops_reading_before_materializing_the_input():
    from oaw_knowledge_base import markdown

    def rows():
        for index in range(markdown.MAX_TABULAR_ROWS + 1):
            assert index < markdown.MAX_TABULAR_ROWS, 'Converter read beyond its row budget'
            yield [index]

    assert markdown._rows_to_markdown_table(rows())


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    last_headers = None
    last_payload = None
    last_url = None
    response = _FakeResponse(200, {"choices": [{"message": {"content": "A photo of a lab notebook page."}}]})

    def __init__(self, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        type(self).last_headers = headers
        type(self).last_payload = json
        type(self).last_url = url
        return type(self).response


def test_vision_engine_requires_both_env_vars(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    with pytest.raises(KnowledgeError):
        to_markdown(b"fakejpegbytes", "notebook.jpg", "image/jpeg")


def test_vision_engine_sends_a_bearer_header_and_the_image_as_a_data_url(monkeypatch):
    monkeypatch.setenv("OAW_VISION_API_KEY", "test-key-123")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr("httpx.Client", _FakeClient)

    text, engine, metadata = to_markdown(b"fakejpegbytes", "notebook.jpg", "image/jpeg")

    assert engine == "vision"
    assert metadata["engine"] == "vision"
    assert text == "A photo of a lab notebook page."
    assert _FakeClient.last_url == "https://vision.example/v1/chat/completions"
    expected_auth = "Bear" + "er " + "test-key-123"
    assert _FakeClient.last_headers["Authorization"] == expected_auth
    content = _FakeClient.last_payload["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vision_engine_uses_the_configured_model_or_a_default(monkeypatch):
    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr("httpx.Client", _FakeClient)

    to_markdown(b"bytes", "chart.png", "image/png")
    assert _FakeClient.last_payload["model"] == "gpt-4o-mini"

    monkeypatch.setenv("OAW_VISION_MODEL", "custom-vision-model")
    to_markdown(b"bytes", "chart.png", "image/png")
    assert _FakeClient.last_payload["model"] == "custom-vision-model"


def test_vision_engine_reports_a_non_200_response_as_a_knowledge_error(monkeypatch):
    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    _FakeClient.response = _FakeResponse(401, {})
    monkeypatch.setattr("httpx.Client", _FakeClient)
    try:
        with pytest.raises(KnowledgeError):
            to_markdown(b"bytes", "chart.png", "image/png")
    finally:
        _FakeClient.response = _FakeResponse(
            200, {"choices": [{"message": {"content": "ok"}}]})


def test_vision_engine_reports_a_malformed_body_as_a_knowledge_error(monkeypatch):
    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    _FakeClient.response = _FakeResponse(200, {"unexpected": "shape"})
    monkeypatch.setattr("httpx.Client", _FakeClient)
    try:
        with pytest.raises(KnowledgeError):
            to_markdown(b"bytes", "chart.png", "image/png")
    finally:
        _FakeClient.response = _FakeResponse(
            200, {"choices": [{"message": {"content": "ok"}}]})


def test_image_detected_by_extension_when_media_type_is_generic(monkeypatch):
    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr("httpx.Client", _FakeClient)
    text, engine, _ = to_markdown(b"bytes", "photo.png", "application/octet-stream")
    assert engine == "vision"
    assert _FakeClient.last_payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,")


@pytest.mark.parametrize('installed', [True, False])
def test_available_engines_never_loads_native_pdf_dependencies(monkeypatch, installed):
    from oaw_knowledge_base import markdown
    monkeypatch.setattr(markdown, 'find_spec', lambda name: ModuleSpec(name, loader=None) if installed else None)
    original = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in {'pymupdf4llm', 'pymupdf', 'numpy'}:
            raise AssertionError('Engine discovery must not initialize native PDF dependencies')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    assert ('pymupdf4llm' in available_engines()) is installed


def test_available_engines_reports_vision_only_when_both_env_vars_are_set(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    assert "vision" not in available_engines()

    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    assert "vision" not in available_engines()

    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    assert "vision" in available_engines()


def test_xlsx_conversion_keeps_native_imports_out_of_the_host(monkeypatch):
    from oaw_knowledge_base import markdown
    data = _xlsx_bytes({'Results': [['Sample', 'Value'], ['A', 3.2]]})
    original = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in {'openpyxl', 'numpy', 'pymupdf4llm', 'pymupdf'}:
            raise AssertionError('Native conversion dependencies belong in the worker process')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    assert '| A | 3.2 |' in markdown.to_markdown(data, 'results.xlsx', '')[0]


def test_native_conversion_timeout_is_actionable_and_keeps_the_source_retryable(monkeypatch):
    from oaw_knowledge_base import markdown
    def expired(*args, **kwargs):
        raise markdown.subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(markdown.subprocess, 'run', expired)
    with pytest.raises(KnowledgeError, match='source is retained; retry its conversion'):
        markdown.to_markdown(b'xlsx fixture', 'results.xlsx', '')


class _Connection:
    def __init__(self, adapter="openai", model_id="gpt-4o", base_url="https://host.example/v1", api_key="host-key"):
        self.adapter, self.model_id, self.base_url, self.api_key = adapter, model_id, base_url, api_key


def test_vision_prefers_the_card_model_then_the_default_model_then_the_variables(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    monkeypatch.setattr("httpx.Client", _FakeClient)
    asked = []

    def resolve_model(reference):
        asked.append(reference)
        if reference == "oaw:model:broken":
            raise ValueError("Selected model connection is missing")
        return _Connection(model_id="openai/gpt-4o" if reference == "oaw:default" else "pixtral")

    # An explicit pick is resolved as given.
    to_markdown(b"bytes", "chart.png", "image/png", vision_model="oaw:model:m1", resolve_model=resolve_model)
    assert asked[-1] == "oaw:model:m1"
    assert _FakeClient.last_payload["model"] == "pixtral"
    assert _FakeClient.last_url == "https://host.example/v1/chat/completions"
    assert _FakeClient.last_headers["Authorization"].endswith("host-key")

    # Blank means the OAW default model; a provider prefix is stripped for the wire.
    to_markdown(b"bytes", "chart.png", "image/png", vision_model="", resolve_model=resolve_model)
    assert asked[-1] == "oaw:default"
    assert _FakeClient.last_payload["model"] == "gpt-4o"

    # An explicit pick that cannot be resolved fails loudly rather than falling back.
    with pytest.raises(KnowledgeError, match="broken"):
        to_markdown(b"bytes", "chart.png", "image/png", vision_model="oaw:model:broken",
                    resolve_model=resolve_model)


def test_vision_falls_back_to_the_variables_through_the_host_resolver(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    monkeypatch.setattr("httpx.Client", _FakeClient)

    def no_default(reference):
        raise ValueError("Choose a default model")

    # The global key:value settings arrive through resolve_secret, not os.environ.
    variables = {"OAW_VISION_API_KEY": "settings-key", "OAW_VISION_BASE_URL": "https://settings.example/v1",
                 "OAW_VISION_MODEL": "settings-vision"}
    text, engine, _ = to_markdown(b"bytes", "chart.png", "image/png", resolve_model=no_default,
                                  resolve_secret=variables.get)
    assert engine == "vision"
    assert _FakeClient.last_url == "https://settings.example/v1/chat/completions"
    assert _FakeClient.last_payload["model"] == "settings-vision"
    assert _FakeClient.last_headers["Authorization"].endswith("settings-key")

    # Nothing anywhere: the error names both ways to fix it.
    with pytest.raises(KnowledgeError, match="Vision model in the card's Settings"):
        to_markdown(b"bytes", "chart.png", "image/png", resolve_model=no_default, resolve_secret=lambda _: None)


def test_vision_refuses_a_non_openai_compatible_connection(monkeypatch):
    monkeypatch.setattr("httpx.Client", _FakeClient)
    with pytest.raises(KnowledgeError, match="OpenAI-compatible"):
        to_markdown(b"bytes", "chart.png", "image/png",
                    resolve_model=lambda _: _Connection(adapter="anthropic"))


def test_available_engines_reports_vision_through_the_model_connection(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    assert "vision" in available_engines(resolve_model=lambda _: _Connection())

    def nothing(reference):
        raise ValueError("no default")
    assert "vision" not in available_engines(resolve_model=nothing)
    assert "vision" in available_engines(resolve_model=nothing, resolve_secret={
        "OAW_VISION_API_KEY": "k", "OAW_VISION_BASE_URL": "u"}.get)
