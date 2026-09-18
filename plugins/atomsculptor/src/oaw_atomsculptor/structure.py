"""Portable, revisioned atom-structure document contracts.

The structure document is deliberately independent of a Sandbox path.  A
Sandbox can import or export a structure only through an explicit OAW tool;
the browser editor never receives ambient filesystem access.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Atom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    symbol: str = Field(min_length=1, max_length=3, pattern=r"^[A-Z][a-z]?$" )
    x: float
    y: float
    z: float
    layer_id: str = Field(default="atoms", min_length=1, max_length=120)
    # Format-specific values are structured rather than discarded on import.
    # They remain optional so the canonical atom model stays useful for agents.
    label: str = Field(default="", max_length=120)
    occupancy: float | None = None
    selective_dynamics: tuple[bool, bool, bool] | None = None
    metadata: dict[str, str | float | bool | None] = Field(default_factory=dict)


class Bond(BaseModel):
    """An explicit connection imported from a chemistry format."""

    model_config = ConfigDict(extra="forbid")

    first_atom_id: int = Field(ge=0)
    second_atom_id: int = Field(ge=0)
    order: str = Field(default="1", min_length=1, max_length=16)
    metadata: dict[str, str | float | bool | None] = Field(default_factory=dict)


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    # ``lattice`` plus the per-layer fields below are part of AtomSculptor's
    # original LXYZ model.  Keeping them in the canonical OAW document means
    # importing an existing AtomSculptor project cannot silently flatten it to
    # one global cell.
    kind: Literal["atoms", "lattice", "selection", "annotation"] = "atoms"
    visible: bool = True
    cell: list[list[float]] | None = None
    pbc: tuple[bool, bool, bool] | None = None
    metadata: str = Field(default="", max_length=8_192)


class StructureDocument(BaseModel):
    """The canonical editable model for one AtomSculptor Structure card."""

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    atoms: list[Atom] = Field(default_factory=list, max_length=20_000)
    bonds: list[Bond] = Field(default_factory=list, max_length=100_000)
    cell: list[list[float]] | None = None
    pbc: tuple[bool, bool, bool] = (False, False, False)
    layers: list[Layer] = Field(default_factory=lambda: [Layer(id="atoms", name="Atoms")], max_length=256)
    active_layer_ids: list[str] = Field(default_factory=lambda: ["atoms"], max_length=256)
    selected_atom_ids: list[int] = Field(default_factory=list, max_length=20_000)
    source_name: str = Field(default="", max_length=255)
    source_metadata: dict[str, str | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_structure(self) -> "StructureDocument":
        ids = [atom.id for atom in self.atoms]
        if len(ids) != len(set(ids)):
            raise ValueError("atom ids must be unique")
        layer_ids = [layer.id for layer in self.layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("layer ids must be unique")
        if any(atom.layer_id not in layer_ids for atom in self.atoms):
            raise ValueError("every atom must belong to a declared layer")
        if any(layer_id not in layer_ids for layer_id in self.active_layer_ids):
            raise ValueError("active_layer_ids must refer to declared layers")
        if any(atom_id not in set(ids) for atom_id in self.selected_atom_ids):
            raise ValueError("selected_atom_ids must refer to existing atoms")
        if any(bond.first_atom_id not in set(ids) or bond.second_atom_id not in set(ids) or bond.first_atom_id == bond.second_atom_id for bond in self.bonds):
            raise ValueError("every bond must connect two distinct existing atoms")
        if self.cell is not None and (len(self.cell) != 3 or any(len(row) != 3 for row in self.cell)):
            raise ValueError("cell must be a 3 by 3 matrix")
        if any(layer.cell is not None and (len(layer.cell) != 3 or any(len(row) != 3 for row in layer.cell)) for layer in self.layers):
            raise ValueError("layer cell must be a 3 by 3 matrix")
        return self


class ReplaceStructure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    structure: StructureDocument


class SelectAtoms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atom_ids: list[int] = Field(default_factory=list, max_length=20_000)


class SelectLayers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer_ids: list[str] = Field(default_factory=list, max_length=256)


def replace(value: dict, arguments: dict) -> dict:
    request = ReplaceStructure.model_validate(arguments)
    return request.structure.model_dump(mode="json")


def select(value: dict, arguments: dict) -> dict:
    request = SelectAtoms.model_validate(arguments)
    document = StructureDocument.model_validate(value)
    available = {atom.id for atom in document.atoms}
    if any(atom_id not in available for atom_id in request.atom_ids):
        raise ValueError("selected atoms must be present in the current structure")
    return document.model_copy(update={"selected_atom_ids": request.atom_ids}).model_dump(mode="json")


def select_layers(value: dict, arguments: dict) -> dict:
    request = SelectLayers.model_validate(arguments)
    document = StructureDocument.model_validate(value)
    available = {layer.id for layer in document.layers}
    if any(layer_id not in available for layer_id in request.layer_ids):
        raise ValueError("active layers must be declared in the current structure")
    return document.model_copy(update={"active_layer_ids": request.layer_ids}).model_dump(mode="json")


def summary(value: dict) -> dict:
    document = StructureDocument.model_validate(value)
    return {
        "atom_count": len(document.atoms),
        "layer_count": len(document.layers),
        "active_layer_count": len(document.active_layer_ids),
        "selected_atom_count": len(document.selected_atom_ids),
        "source_name": document.source_name,
    }
