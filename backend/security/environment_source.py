"""Optional operator-owned execution defaults, separate from process environment."""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from backend.execution_config import SecretRequirement


@dataclass(frozen=True)
class ExecutionEnvironmentSnapshot:
    variables: Mapping[str, "str | SecretRequirement"]
    secrets: Mapping[str, str] = field(repr=False)


class ExecutionEnvironmentSource(Protocol):
    def read(self) -> ExecutionEnvironmentSnapshot: ...
