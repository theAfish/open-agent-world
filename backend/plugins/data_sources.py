"""Read-only, bounded datasets shared by independently installed Packs."""
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.plugins.resources import NodeResourceContext


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_id: str = Field(min_length=1, max_length=512)
    columns: list[str] = Field(default_factory=list, max_length=32)
    group_by: str | None = None
    aggregate: Literal["none", "count", "sum", "mean", "min", "max"] = "none"
    value: str | None = None
    order_by: str | None = None
    descending: bool = False
    limit: int = Field(default=2000, ge=1, le=10000)
    entity_type: str | None = Field(default=None, max_length=200)
    relation_type: str | None = Field(default=None, max_length=200)


class NodeDataConsumer(BaseModel):
    """A Pack declares where the host stores its connection-time selection."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_field: str = Field(min_length=1)
    schema_field: str = Field(min_length=1)
    kinds: tuple[Literal["table", "graph"], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_fields(self):
        if self.source_field == self.schema_field:
            raise ValueError("data consumer source and schema fields must be distinct")
        return self


@dataclass(frozen=True, slots=True)
class NodeDataSource:
    """schemas(context) lists datasets; read(context, query) returns table/graph data.

    Handlers run under the resource mutation lock, off the event loop. They must
    respect cancellation, bound work, and never mutate user data. See the plugin
    data-source documentation for the JSON response contract.
    """
    schemas: Callable[[NodeResourceContext], dict]
    read: Callable[[NodeResourceContext, DataQuery], dict]
