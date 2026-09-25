"""Marketplace V0 HTTP only. No installation, discovery or runtime ownership."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import re
from typing import Annotated, TypeVar
from urllib.parse import quote, urlsplit

import httpx
from packaging.version import Version
from pydantic import BaseModel, Field, field_validator

from backend.packs.archive import MAX_ARCHIVE_BYTES

PACK_MIME = "application/vnd.oaw.pack"
PackId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$", max_length=120)]
VersionId = Annotated[str, Field(min_length=1, max_length=64)]


class Identity(BaseModel):
    pack_id: PackId
    version: VersionId

    @field_validator("version")
    @classmethod
    def canonical(cls, value):
        if str(Version(value)) != value:
            raise ValueError("Expected a canonical PEP 440 version")
        return value


class Listing(BaseModel):
    id: PackId
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=20000)
    latest_version: VersionId | None

    @field_validator("latest_version")
    @classmethod
    def canonical(cls, value):
        return Identity.canonical(value) if value is not None else None


class PackDetail(Listing):
    versions: list[VersionId] = Field(max_length=100)
    versions_next_cursor: str | None = None


class PackPage(BaseModel):
    items: list[Listing] = Field(max_length=100)
    next_cursor: str | None


# Display metadata is deliberately narrower than the installation manifest.
# It does not enforce host compatibility or resolve dependencies.
class Compatibility(BaseModel):
    oaw: str
    plugin_api: str
    frontend_api: int


class Dependency(BaseModel):
    id: str
    version: str = ""


class Dependencies(BaseModel):
    packs: list[Dependency] = Field(default_factory=list)


class Sandbox(BaseModel):
    python: list[str] = Field(default_factory=list)


class Runtime(BaseModel):
    sandbox: Sandbox = Field(default_factory=Sandbox)


class DisplayManifest(BaseModel):
    id: str
    version: str
    compatibility: Compatibility
    dependencies: Dependencies = Field(default_factory=Dependencies)
    runtime: Runtime = Field(default_factory=Runtime)


class PackVersion(Identity):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES, strict=True)
    manifest: DisplayManifest


class VersionPage(BaseModel):
    items: list[PackVersion] = Field(max_length=100)
    next_cursor: str | None


class MarketplaceError(Exception):
    def __init__(self, message: str, status_code=502):
        super().__init__(message)
        self.status_code = status_code


M = TypeVar("M", bound=BaseModel)


class MarketplaceClient:
    def __init__(self, url: str | None, *, transport=None, request_timeout=30.0, download_timeout=300.0):
        self.url = (url or "").rstrip("/")
        self.transport = transport
        self.request_timeout = request_timeout
        self.download_timeout = download_timeout

    @asynccontextmanager
    async def _http(self, deadline):
        # Validation and all network work are lazy: even bad configuration cannot
        # prevent the local world from starting. Redirects never leave this API.
        if not self.url:
            raise MarketplaceError("Store is not configured yet.", 503)
        try:
            parsed = urlsplit(self.url)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise MarketplaceError("Store is unavailable. Check the host configuration.", 503)
        try:
            async with asyncio.timeout(deadline):
                async with httpx.AsyncClient(
                    base_url=self.url + "/", transport=self.transport, follow_redirects=False,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    headers={"Accept-Encoding": "identity"},
                ) as client:
                    yield client
        except (TimeoutError, httpx.TimeoutException):
            raise MarketplaceError("Store request timed out. Please retry.", 504) from None
        except httpx.RequestError:
            raise MarketplaceError("Store connection failed or download was interrupted. Please retry.", 503) from None

    @staticmethod
    def _response(response):
        if response.status_code == 200:
            return
        if response.status_code == 404:
            raise MarketplaceError("This Pack or version is no longer available.", 404)
        if response.status_code == 429:
            raise MarketplaceError("Store is busy. Please retry shortly.", 429)
        if response.status_code == 504:
            raise MarketplaceError("Store request timed out. Please retry.", 504)
        # Never copy upstream error JSON, URLs, headers or credential details.
        raise MarketplaceError("Store is temporarily unavailable. Please retry.", 503)

    async def _json(self, path: str, model: type[M], params=None) -> M:
        async with self._http(self.request_timeout) as client:
            async with client.stream("GET", path, params=params, headers={"Accept": "application/json"}) as response:
                self._response(response)
                if (response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise MarketplaceError("Store returned invalid metadata.")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > 8 * 1024 * 1024:
                        raise MarketplaceError("Store metadata exceeds the size limit.")
                    body.extend(chunk)
                try:
                    return model.model_validate_json(body)
                except ValueError:
                    raise MarketplaceError("Store returned invalid metadata.") from None

    @staticmethod
    def _path(pack_id, version=None):
        # The frontend supplies identities only, never a URL or provider path.
        Identity(pack_id=pack_id, version=version or "0")
        path = f"v1/packs/{quote(pack_id, safe='')}"
        return path if version is None else f"{path}/versions/{quote(version, safe='')}"

    async def list_packs(self, *, query=None, cursor=None, limit=20):
        return await self._json("v1/packs", PackPage, {k: v for k, v in
            {"query": query, "cursor": cursor, "limit": limit}.items() if v is not None})

    async def get_pack(self, pack_id):
        result = await self._json(self._path(pack_id), PackDetail)
        if result.id != pack_id:
            raise MarketplaceError("Store returned a different Pack.")
        return result

    async def list_versions(self, pack_id, *, cursor=None, limit=20):
        result = await self._json(self._path(pack_id) + "/versions", VersionPage,
            {k: v for k, v in {"cursor": cursor, "limit": limit}.items() if v is not None})
        for item in result.items:
            self._identity(item, pack_id, item.version)
        return result

    @staticmethod
    def _identity(item, pack_id, version):
        if (item.pack_id, item.version, item.manifest.id, item.manifest.version) != (pack_id, version, pack_id, version):
            raise MarketplaceError("Store returned a different Pack or version.")

    async def get_version(self, pack_id, version):
        result = await self._json(self._path(pack_id, version), PackVersion)
        self._identity(result, pack_id, version)
        return result

    async def download(self, metadata: PackVersion) -> bytes:
        async with self._http(self.download_timeout) as client:
            async with client.stream("GET", self._path(metadata.pack_id, metadata.version) + "/download",
                                     headers={"Accept": PACK_MIME}) as response:
                self._response(response)
                if response.headers.get("content-type", "").split(";")[0].strip().lower() != PACK_MIME:
                    raise MarketplaceError("Store download has an invalid file type.")
                length = response.headers.get("content-length", "")
                if not re.fullmatch(r"[0-9]{1,12}", length) or int(length) != metadata.size_bytes:
                    raise MarketplaceError("Store download size does not match its version.")
                if response.headers.get("x-oaw-pack-sha256") != metadata.sha256:
                    raise MarketplaceError("Store download SHA-256 does not match its version.")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise MarketplaceError("Store download has an unsupported encoding.")
                body = bytearray()
                digest = hashlib.sha256()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > min(metadata.size_bytes, MAX_ARCHIVE_BYTES):
                        raise MarketplaceError("Store download exceeds its expected size.")
                    body.extend(chunk)
                    digest.update(chunk)
                if len(body) != metadata.size_bytes:
                    raise MarketplaceError("Store download is incomplete. Please retry.")
                if digest.hexdigest() != metadata.sha256:
                    raise MarketplaceError("Store download failed SHA-256 verification. Please retry.")
                # No temporary files survive exceptions or cancellation.
                return bytes(body)
