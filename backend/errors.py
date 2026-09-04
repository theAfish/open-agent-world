from __future__ import annotations


class DomainError(Exception):
    """Base class for errors safe to expose through the API."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class GraphValidationError(DomainError):
    code = "invalid_relationship"


class PermissionDeniedError(DomainError):
    code = "permission_denied"


class RevisionConflictError(ConflictError):
    code = "revision_conflict"


class UnsafePathError(DomainError):
    code = "unsafe_path"


class ResourceValidationError(DomainError):
    code = "invalid_resource"


class ConversationValidationError(DomainError):
    code = "invalid_conversation"


class RuntimeUnavailableError(DomainError):
    code = "runtime_unavailable"


class PluginCompatibilityError(DomainError):
    code = "plugin_incompatible"


class PluginUnavailableError(DomainError):
    code = "plugin_unavailable"
