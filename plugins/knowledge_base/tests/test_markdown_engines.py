"""markdown.py's tabular engines in isolation: CSV/TSV/XLSX -> a real markdown table."""
import io

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


def test_available_engines_reports_vision_only_when_both_env_vars_are_set(monkeypatch):
    monkeypatch.delenv("OAW_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OAW_VISION_BASE_URL", raising=False)
    assert "vision" not in available_engines()

    monkeypatch.setenv("OAW_VISION_API_KEY", "k")
    assert "vision" not in available_engines()

    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    assert "vision" in available_engines()
