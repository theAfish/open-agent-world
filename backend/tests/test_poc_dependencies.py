"""Pinned POC dependencies use real Store verification and Pack lifecycle."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from backend import development, poc
from backend.errors import ConflictError
from backend.packs.marketplace import Identity, MarketplaceClient
from backend.poc_dependencies import read_requirements, synchronize
from backend.storage_location import file_lock
from backend.tests.test_marketplace import FakeMarketplace, Stream
from backend.tests.test_pack_installation import MANIFEST, artifact
from backend.tests.test_poc import isolated_profiles  # noqa: F401


REQUIRED = [Identity(pack_id="test.greeter", version="0.1.0")]


def store(services, fake):
    services.marketplace = MarketplaceClient("https://marketplace.test", transport=httpx.MockTransport(fake))


@pytest.mark.asyncio
async def test_exact_version_install_and_offline_reuse():
    fake = FakeMarketplace()
    with poc.open_profile("poc", initialize=True) as services:
        store(services, fake)
        try:
            result = await synchronize(services, REQUIRED)
            assert result[0]["status"] == "ready"
            assert [r.url.path for r in fake.calls] == [
                "/v1/packs/test.greeter/versions/0.1.0", "/v1/packs/test.greeter/versions/0.1.0/download"]
            fake.calls.clear()
            fake.status = 503
            assert await synchronize(services, REQUIRED) == result
            assert fake.calls == []
        finally:
            await services.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unpublished", "incompatible", "corrupt"])
async def test_store_failure_does_not_install_or_fall_back(failure):
    manifest = deepcopy(MANIFEST)
    if failure == "incompatible":
        manifest["compatibility"]["oaw"] = ">=999"
    fake = FakeMarketplace(manifest)
    if failure == "unpublished":
        fake.status = 404

    def transport(request):
        response = fake(request)
        if failure == "corrupt" and request.url.path.endswith("/download"):
            return httpx.Response(200, headers=response.headers, stream=Stream([b"x" * len(fake.data)]))
        return response

    with poc.open_profile("poc", initialize=True) as services:
        store(services, transport)
        try:
            with pytest.raises(ValueError, match={"unpublished": "not published", "incompatible": "requires OAW", "corrupt": "SHA-256"}[failure]):
                await synchronize(services, REQUIRED)
            assert services.pack_installations.selected() == {}
            assert len(fake.calls) == (2 if failure == "corrupt" else 1)
        finally:
            await services.shutdown()


@pytest.mark.asyncio
async def test_local_override_validates_identity_and_immutable_bytes_before_reuse(tmp_path):
    path = tmp_path / "team.oawpack"
    wrong = deepcopy(MANIFEST)
    wrong["version"] = "0.2.0"
    path.write_bytes(artifact(wrong))
    fake = FakeMarketplace()
    with poc.open_profile("poc", initialize=True) as services:
        store(services, fake)
        try:
            with pytest.raises(ValueError, match="does not match"):
                await synchronize(services, REQUIRED, [path])
            assert services.pack_installations.selected() == {}
            path.write_bytes(fake.data)
            assert (await synchronize(services, REQUIRED, [path]))[0]["status"] == "ready"
            replacement = deepcopy(MANIFEST)
            replacement["name"] = "Replaced archive"
            path.write_bytes(artifact(replacement))
            with pytest.raises(ValueError, match="immutable"):
                await synchronize(services, REQUIRED, [path])
            assert fake.calls == []
        finally:
            await services.shutdown()


@pytest.mark.asyncio
async def test_existing_older_pack_upgrades_but_newer_pack_is_not_downgraded():
    new_manifest = deepcopy(MANIFEST)
    new_manifest["version"] = "0.2.0"
    fake = FakeMarketplace(new_manifest)
    with poc.open_profile("poc", initialize=True) as services:
        store(services, fake)
        try:
            services.pack_installations.install(artifact())
            required = [Identity(pack_id="test.greeter", version="0.2.0")]
            assert (await synchronize(services, required))[0]["installed_version"] == "0.2.0"
            fake.calls.clear()
            with pytest.raises(ValueError, match="will not downgrade"):
                await synchronize(services, REQUIRED)
            assert fake.calls == []
            services.pack_installations.activate("test.greeter", "0.1.0")
            assert (await synchronize(services, required))[0]["status"] == "ready"
            assert [r.url.path for r in fake.calls] == ["/v1/packs/test.greeter/versions/0.2.0"]
        finally:
            await services.shutdown()


def test_dependency_diagnostic_and_installer_share_profile_lock(tmp_path, capsys, monkeypatch):
    requirements = tmp_path / "packs.toml"
    requirements.write_text('[[packs]]\npack_id="test.greeter"\nversion="0.1.0"\n')
    root = development.prepare_profile("poc")
    monkeypatch.setenv("OPEN_AGENT_WORLD_MARKETPLACE_URL", "")
    with poc.open_profile("poc") as services:
        assert services.settings.marketplace_url is None
    assert poc.main(["deps", "--requirements", str(requirements)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["packs"][0]["status"] == "missing"
    assert result["next_step"] == "python scripts/poc.py setup --profile poc"
    with file_lock(root / ".oaw-storage.lock"), pytest.raises(ConflictError, match="in use"):
        asyncio.run(poc.prepare_dependencies("poc", requirements, install=True))
    assert not (root / "packs/installed").exists()


@pytest.mark.parametrize("text", [
    '[[packs]]\npack_id="test.greeter"\nversion=">=0.1"',
    '[[packs]]\npack_id="test.greeter"\nversion="0.1.0"\nurl="http://untrusted.test"',
    '[[packs]]\npack_id="test.greeter"\nversion="0.1.0"\n' * 2,
])
def test_requirements_reject_ambiguous_versions_urls_and_duplicates(tmp_path, text):
    path = tmp_path / "packs.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_requirements(path)
