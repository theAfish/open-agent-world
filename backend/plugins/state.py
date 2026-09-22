"""Card state policy and the session-free plugin persistence contract."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

StateScope = Literal["shared", "session"]


class StatelessStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["none"] = "none"


class ScopedStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    mode: Literal["scoped"] = "scoped"
    supported_scopes: tuple[StateScope, ...] = Field(default=("shared",), alias="supportedScopes", min_length=1)
    default_scope: StateScope = Field(default="shared", alias="defaultScope")
    user_configurable: bool = Field(default=False, alias="userConfigurable")

    @model_validator(mode="after")
    def validate_scopes(self) -> Self:
        if len(set(self.supported_scopes)) != len(self.supported_scopes):
            raise ValueError("supportedScopes must be unique")
        if self.default_scope not in self.supported_scopes:
            raise ValueError("defaultScope must belong to supportedScopes")
        return self


PluginStateSpec = Annotated[StatelessStateSpec | ScopedStateSpec, Field(discriminator="mode")]
LEGACY_STATE = ScopedStateSpec()


class CardStateStore(Protocol):
    """Bound to the invocation's card and namespace by the host.

    Values are JSON objects. Each result contains value and revision. update is
    an atomic shallow merge; delete clears the payload and advances revision.
    No namespace or session identifier is accepted from plugin code.
    """

    def get(self) -> dict[str, Any]: ...
    def set(self, value: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]: ...
    def update(self, patch: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]: ...
    def delete(self, expected_revision: int | None = None) -> dict[str, Any]: ...
