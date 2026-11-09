"""Card state policy and the session-free plugin persistence contract."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

StateScope = Literal["shared", "session", "group"]


class StatelessStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["none"] = "none"


class ScopedStateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    mode: Literal["scoped"] = "scoped"
    supported_scopes: tuple[StateScope, ...] = Field(default=("shared",), alias="supportedScopes", min_length=1)
    default_scope: StateScope = Field(default="shared", alias="defaultScope")
    user_configurable: bool = Field(default=False, alias="userConfigurable")
    follow_workspace: bool = Field(default=False, alias='followWorkspace')

    @model_serializer(mode='wrap')
    def serialize(self, handler):
        value = handler(self)
        if not self.follow_workspace:
            value.pop('followWorkspace', None)
            value.pop('follow_workspace', None)
        return value

    @model_validator(mode="after")
    def validate_scopes(self) -> Self:
        if len(set(self.supported_scopes)) != len(self.supported_scopes):
            raise ValueError("supportedScopes must be unique")
        if self.default_scope not in self.supported_scopes:
            raise ValueError("defaultScope must belong to supportedScopes")
        if self.follow_workspace and not {'shared', 'session', 'group'}.issubset(self.supported_scopes):
            raise ValueError('followWorkspace requires shared, session and group in supportedScopes')
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
