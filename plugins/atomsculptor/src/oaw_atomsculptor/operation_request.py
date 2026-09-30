"""Validate Structure-panel operation data before it reaches an Agent model."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SurfaceParameters(_Parameters):
    miller_indices: tuple[int, int, int]
    layers: int = Field(ge=1, le=200)
    vacuum: float = Field(ge=0, le=500, allow_inf_nan=False)
    need_conventional: bool


class SupercellParameters(_Parameters):
    repetitions: tuple[int, int, int]

    @model_validator(mode="after")
    def positive_repetitions(self) -> "SupercellParameters":
        if any(value < 1 or value > 100 for value in self.repetitions):
            raise ValueError("supercell repetitions must be between 1 and 100")
        return self


class InterfaceParameters(_Parameters):
    second_structure: str = Field(min_length=1, max_length=2048)
    miller_1: tuple[int, int, int]
    miller_2: tuple[int, int, int]
    gap: float = Field(ge=0, le=500, allow_inf_nan=False)
    vacuum_between: float = Field(ge=0, le=500, allow_inf_nan=False)
    thickness_1: float = Field(gt=0, le=500, allow_inf_nan=False)
    thickness_2: float = Field(gt=0, le=500, allow_inf_nan=False)
    max_interfaces: int = Field(ge=1, le=100)


class MoleculeParameters(_Parameters):
    smiles: str = Field(min_length=1, max_length=2048)


class ExportParameters(_Parameters):
    format: Literal["json", "xyz", "extxyz", "lxyz", "cif", "poscar", "pdb", "sdf", "mol2"]
    file_name: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00]+$")
    publish_artifact: bool


class InterfaceSelectParameters(_Parameters):
    candidate: int = Field(ge=1, le=1000)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure_card: str = Field(min_length=1, max_length=120)
    sandbox_hint: str | None = Field(default=None, max_length=120)
    skill_hint: str | None = Field(default=None, max_length=120)
    track_on_task_board: bool = False


class SurfaceRequest(_Request):
    operation: Literal["surface"]
    parameters: SurfaceParameters


class SupercellRequest(_Request):
    operation: Literal["supercell"]
    parameters: SupercellParameters


class InterfaceRequest(_Request):
    operation: Literal["interface"]
    parameters: InterfaceParameters


class MoleculeRequest(_Request):
    operation: Literal["molecule"]
    parameters: MoleculeParameters


class ExportRequest(_Request):
    operation: Literal["export"]
    parameters: ExportParameters


class InterfaceSelectRequest(_Request):
    operation: Literal["interface_select"]
    parameters: InterfaceSelectParameters


OperationRequest = Annotated[
    SurfaceRequest | SupercellRequest | InterfaceRequest | MoleculeRequest | ExportRequest | InterfaceSelectRequest,
    Field(discriminator="operation"),
]
_ADAPTER = TypeAdapter(OperationRequest)
_BLOCK = re.compile(r"(?:^|\n)ATOMSCULPTOR REQUEST\n(?P<data>[^\n]+)\nEND ATOMSCULPTOR REQUEST(?:\n|$)")


def parse_operation_request(prompt: str) -> OperationRequest | None:
    """Return typed data, or None for an ordinary free-form modelling turn."""
    if "ATOMSCULPTOR REQUEST" not in prompt:
        return None
    matches = list(_BLOCK.finditer(prompt))
    if len(matches) != 1:
        raise ValueError("Provide exactly one complete ATOMSCULPTOR REQUEST block")
    raw = matches[0].group("data")
    if len(raw.encode("utf-8")) > 16 * 1024:
        raise ValueError("AtomSculptor operation data exceeds 16 KiB")
    try:
        return _ADAPTER.validate_python(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"Invalid AtomSculptor operation data: {exc}") from exc
