"""Bounded, exact UTF-8 edits through the native workspace file boundary."""
from __future__ import annotations

from .models import SandboxValidationError

TEXT_LIMIT = 256 * 1024


def apply_text_edits(raw: bytes, edits: list) -> bytes:
    if len(raw) > TEXT_LIMIT:
        raise SandboxValidationError("Text file exceeds the 256 KiB edit limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxValidationError("Text edits require an existing UTF-8 file") from exc
    if "\0" in text or not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise SandboxValidationError("Use 1 to 20 exact text edits on a UTF-8 file without NUL bytes")
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {"old", "new"}
                or not isinstance(edit["old"], str) or not edit["old"]
                or not isinstance(edit["new"], str)
                or "\0" in edit["old"] or "\0" in edit["new"]):
            raise SandboxValidationError("Each edit needs nonempty old text and replacement new text")
        if text.count(edit["old"]) != 1:
            raise SandboxValidationError("Each old text must match exactly once; add surrounding context")
        text = text.replace(edit["old"], edit["new"], 1)
    try:
        result = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SandboxValidationError("Replacement must be valid UTF-8") from exc
    if not result or len(result) > TEXT_LIMIT:
        raise SandboxValidationError("Edited file must be between 1 byte and 256 KiB")
    return result
