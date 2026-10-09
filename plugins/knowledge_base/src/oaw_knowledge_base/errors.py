"""The one error type the pipeline raises for anything a caller can fix.

It subclasses ``ValueError`` on purpose. OAW's ``backend/node_resources.py`` already
turns a ``ValueError`` out of a resource action into a ``ResourceValidationError``
carrying ``str(error)``, so the card's 422s stay exactly as they were while the core
keeps no import of the host. The service maps it to 422 itself.
"""
from __future__ import annotations


class KnowledgeError(ValueError):
    """A request the knowledge base refuses, with a message safe to show a caller."""


def operator_message(error):
    """One readable line for a failure the caller can act on, or ``None``.

    Handlers raise :class:`KnowledgeError` for their own rules, but pydantic and MKB
    raise their own types underneath. Every front door has to report those the same
    way it reports ours — a message, not a traceback or a 500 — so the mapping lives
    here rather than three times over.
    """
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        first = error.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        return f"{location}: {first['msg']}" if location else first["msg"]
    if isinstance(error, KnowledgeError):
        return str(error)
    try:
        from mkb.exceptions import MKBError, ProviderError
    except ImportError:  # pragma: no cover - depends on the environment
        return None
    if isinstance(error, ProviderError):
        # Provider text can echo a request, including its credentials. Never forward it.
        return "An external provider failed"
    return str(error) if isinstance(error, MKBError) else None
