"""Authoring fails at the same distribution boundary as archive installation."""
import pytest

from backend.packs.factory_models import PackDesign
from backend.packs.manifest import PackManifest
from backend.packs.spec import read_source_pack
from backend.tests.test_pack_spec import source


@pytest.mark.parametrize("identifier", ["example.my-cards", "example.release", "my-org.my-cards"])
def test_distribution_ids_cross_source_factory_and_archive(identifier, tmp_path):
    root = source(tmp_path)
    path = root / "oaw.pack.toml"
    path.write_text(path.read_text().replace("example.release", identifier), encoding="utf-8")
    assert read_source_pack(root).distribution_id == PackDesign(id=identifier).id
    manifest = PackManifest(schema_version=2, kind="content", id=identifier, name="Cards", version="1.0.0",
        compatibility={"oaw": ">=0.1.0,<1", "plugin_api": "1.35", "frontend_api": 1},
        creator={}, content={"cards": ["content/card.json"]})
    assert manifest.id == identifier


@pytest.mark.parametrize("identifier", ["example/cards", "example:cards", "example.my_cards", "single", "../escape"])
def test_invalid_distribution_ids_fail_before_build(identifier, tmp_path):
    root = source(tmp_path)
    path = root / "oaw.pack.toml"
    path.write_text(path.read_text().replace("example.release", identifier), encoding="utf-8")
    with pytest.raises(ValueError):
        read_source_pack(root)
    with pytest.raises(ValueError):
        PackDesign(id=identifier)


@pytest.mark.parametrize("identifier", ["example.cards-", "example.my--cards"])
def test_factory_pack_id_must_also_be_a_valid_runtime_owner(identifier):
    with pytest.raises(ValueError, match="runtime"):
        PackDesign(id=identifier)
