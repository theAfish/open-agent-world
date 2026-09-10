"""Wire contracts contain references and bounded metadata, never file bytes."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    collection_id: str
    version_id: str


class ArtifactPublish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=128)
    sandbox_id: str
    paths: list[str] = Field(min_length=1, max_length=100,
        description='Sandbox-relative file or directory paths as strings, e.g. ["math_flower.png"].')
    finalized: bool
    name: str = Field(min_length=1, max_length=255)
    artifact_id: str | None = None
    inputs: list[ArtifactInput] = Field(default_factory=list, max_length=100,
        description='Optional provenance references to already published artifacts, each with collection_id and version_id. Omit or use [] when none; Sandbox files belong in paths.')


class ArtifactMaterialize(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sandbox_id: str
    destination: str = Field(min_length=1, max_length=1024)


class ArtifactCollectionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['available'] = 'available'
