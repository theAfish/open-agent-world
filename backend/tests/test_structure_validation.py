"""Invalid structure input must be rejected before changing its saved revision."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.tests.conftest import create_node
from oaw_atomsculptor.structure import StructureDocument


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["coordinate", "cell", "layer_cell", "occupancy", "candidate_area", "candidate_strain"])
def test_nonfinite_structure_write_preserves_document(client, invalid, field):
    node = create_node(client, "atomsculptor.structure")
    path = f"/api/nodes/{node['id']}"
    original = client.get(path + "/document").json()
    value = deepcopy(original["value"])
    value["atoms"] = [{"id": 0, "symbol": "C", "x": 0, "y": 0, "z": 0}]
    if field == "coordinate":
        value["atoms"][0]["x"] = invalid
    elif field == "occupancy":
        value["atoms"][0]["occupancy"] = invalid
    elif field in {"cell", "layer_cell"}:
        matrix = [[invalid, 0, 0], [0, 1, 0], [0, 0, 1]]
        if field == "cell":
            value["cell"] = matrix
        else:
            value["layers"][0]["cell"] = matrix
    else:
        value["interface_candidates"] = [{"id": 1, "file_name": "candidate.cif",
            "area" if field == "candidate_area" else "von_mises_strain": invalid}]
    response = client.post(path + "/actions/replace", json={"expected_revision": original["revision"], "arguments": value})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_resource"
    assert client.get(path + "/document").json() == original


@pytest.mark.parametrize("field", ["metadata", "source_metadata", "bond_metadata"])
def test_nonfinite_numeric_metadata_is_rejected(field):
    value = {"atoms": [{"id": 0, "symbol": "C", "x": 0, "y": 0, "z": 0},
                       {"id": 1, "symbol": "H", "x": 1, "y": 0, "z": 0}]}
    if field == "metadata":
        value["atoms"][0]["metadata"] = {"invalid": float("nan")}
    elif field == "source_metadata":
        value["source_metadata"] = {"invalid": float("inf")}
    else:
        value["bonds"] = [{"first_atom_id": 0, "second_atom_id": 1, "metadata": {"invalid": float("-inf")}}]
    with pytest.raises(ValidationError):
        StructureDocument.model_validate(value)


def test_maximum_structure_selection_and_bonds_are_valid():
    count = 20_000
    value = {"atoms": [{"id": i, "symbol": "C", "x": i, "y": 0, "z": 0} for i in range(count)],
             "selected_atom_ids": list(range(count)),
             "bonds": [{"first_atom_id": i % count, "second_atom_id": (i + 1) % count} for i in range(100_000)]}
    document = StructureDocument.model_validate(value)
    assert len(document.selected_atom_ids) == count
    assert len(document.bonds) == 100_000
    value["selected_atom_ids"][-1] = count
    with pytest.raises(ValidationError, match="existing atoms"):
        StructureDocument.model_validate(value)
