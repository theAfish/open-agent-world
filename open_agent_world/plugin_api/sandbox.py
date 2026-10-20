"""Pure command and text-edit helpers for authorized Sandbox operations.

These helpers do not execute commands or grant Sandbox access. Plugins still
invoke the live host capability provider for every filesystem or process action.
"""
from backend.sandbox.commands import python_entrypoint
from backend.sandbox.models import SandboxValidationError
from backend.sandbox.text_edits import apply_text_edits

__all__ = ["python_entrypoint", "SandboxValidationError", "apply_text_edits"]
