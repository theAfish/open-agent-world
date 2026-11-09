"""Bounded, exact UTF-8 edits through the native workspace file boundary."""
from __future__ import annotations

from .models import SandboxValidationError

TEXT_LIMIT = 256 * 1024
MAX_TEXT_EDITS = 20
TEXT_EDITS_SCHEMA = {"type": "array", "minItems": 1, "maxItems": MAX_TEXT_EDITS, "items": {
    "type": "object", "properties": {"old": {"type": "string", "minLength": 1},
        "new": {"type": "string"}}, "required": ["old", "new"], "additionalProperties": False},
    "description": "Sequential exact replacements; include enough context for each old text to occur once."}


def apply_text_edits(raw: bytes, edits: list) -> bytes:
    if len(raw) > TEXT_LIMIT:
        raise SandboxValidationError("Text file exceeds the 256 KiB edit limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxValidationError("Text edits require an existing UTF-8 file") from exc
    if "\0" in text or not isinstance(edits, list) or not 1 <= len(edits) <= MAX_TEXT_EDITS:
        raise SandboxValidationError(f"Use 1 to {MAX_TEXT_EDITS} exact text edits on a UTF-8 file without NUL bytes")
    for index, edit in enumerate(edits, 1):
        if (not isinstance(edit, dict) or set(edit) != {"old", "new"}
                or not isinstance(edit["old"], str) or not edit["old"]
                or not isinstance(edit["new"], str)
                or "\0" in edit["old"] or "\0" in edit["new"]):
            raise SandboxValidationError("Each edit needs nonempty old text and replacement new text")
        count = text.count(edit["old"])
        if count != 1:
            # Report positions in the sequential preview, never modify the
            # file or guess a fuzzy replacement. Bound diagnostics for repeats.
            positions, start = [], 0
            for _ in range(min(count, 5)):
                start = text.find(edit["old"], start)
                positions.append(text.count("\n", 0, start) + 1)
                start += len(edit["old"])
            locations = f"; matching start lines in the sequential edit preview: {positions}" if positions else ""
            raise SandboxValidationError(
                f"Edit {index}: old text matched {count} times; expected exactly once{locations}. "
                "Read the current source around the intended change; copy its exact text and add unique surrounding context. "
                "No file was written.")
        text = text.replace(edit["old"], edit["new"], 1)
    try:
        result = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SandboxValidationError("Replacement must be valid UTF-8") from exc
    if not result or len(result) > TEXT_LIMIT:
        raise SandboxValidationError("Edited file must be between 1 byte and 256 KiB")
    return result
