# Store protocol and installation boundary

For browsing and installation, see [Packs and cards](user-guide/plugins.md).
The OAW client uses the official Marketplace origin from
[official_marketplace.py](../backend/official_marketplace.py). For development,
`OPEN_AGENT_WORLD_MARKETPLACE_URL` overrides that origin; an explicit empty value
disables Store. No ordinary Settings field changes it. Startup performs no
Marketplace network request, and local Packs remain usable when Store is unavailable.

## Ownership and HTTP

```text
Frontend → local OAW Store API → MarketplaceClient → canonical .oawpack bytes
                                                        ↓
Local .oawpack bytes ───────────────────────→ PackInstallationManager
                                                        ↓
                              existing restart / registry / Library / runtime
```

`backend/packs/marketplace.py` only implements the public Marketplace V0 HTTP
protocol. It does not call Card Library, PluginRegistry, Sandbox provisioning or
installation. HTTP clients are created on demand and closed after each request;
startup performs no Marketplace I/O. HTTP construction is centralized so future
authorization headers can be added without changing the installer or frontend.

Host endpoints:

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/store/packs?query=&cursor=&limit=20` | Bounded catalog and local installation state |
| GET | `/api/store/packs/{pack_id}` | Listing and version identifiers |
| GET | `/api/store/packs/{pack_id}/versions?cursor=&limit=20` | Version metadata pagination |
| GET | `/api/store/packs/{pack_id}/versions/{version}` | Explicit version and display requirements |
| POST | `/api/store/packs/{pack_id}/versions/{version}/install` | Acquire and select through the existing installer |

POST requires the same `X-OAW-Pack-Install: 1` header and local control-plane
authorization as local file installation. The frontend never supplies a URL,
requests Marketplace directly or receives storage metadata. Display DTOs keep
only listing/version fields and compatibility, Pack dependencies and Sandbox
Python requirements. Upstream exception text and error payloads are not returned.

The backend merges the current selected/loaded state with each listing using
`packaging.version.Version` (PEP 440), including prerelease ordering. The frontend
receives `installed_version`, `loaded_version`, `available_version`,
`update_available`, `restart_required` and `can_install`. It does not compare
versions. Retained versions remain managed by `PackInstallationManager.activate`;
an already selected immutable version is an idempotent Store result. The local
file endpoint retains its existing duplicate-install behavior.

## Pack previews

Store catalog entries and details reuse `PackSurface`, the same procedural
geometry and materials used by owned Packs. Rendering starts near the viewport,
shares one GPU context, stops while idle, and releases offscreen models. Pointer
drag and arrow keys rotate a Pack; selecting it opens details. Browsers without
WebGL retain the CSS Pack cover and normal review/install controls.

Public listing and version responses may include `preview` with `packaging`
(`standard`, `premium`, `paper`, `collector`), a six-digit hex `accent_color`,
and a nullable `count`. Marketplace derives these from the immutable Schema 2
manifest's existing `creator` and `content` fields. Detail previews follow the
reviewed version. Missing metadata, including existing Schema 3 distributions,
uses the standard appearance with an unknown count. This does not download
archives, execute Pack code, load remote shaders, or expose local artwork URLs.

`npm run build:store-preview` in `frontend` exports the same component and lazy
renderer into `.tmp/store-preview` for the standalone website. The website's
`scripts/build-pack-preview.mjs` regenerates its committed static assets from
this source; see that checkout's `docs/marketplace.md`. The adapter uses English
copy without OAW profile storage. Publishing the website and deploying the
catalog metadata change are separate from generating these assets locally.

## Acquisition and installation

Every Get/Update fetches fresh metadata for the explicit version. There is no
catalog cache in V0. A download must meet all of these checks:

- `application/vnd.oaw.pack` MIME; no content encoding or HTTP redirects.
- Positive authoritative size within the existing 128 MiB archive limit.
- `Content-Length` equal to the authoritative size, and
  `X-OAW-Pack-SHA256` equal to the authoritative digest.
- Incrementally bounded body, exact final byte count and locally calculated
  SHA-256. Short, oversized, interrupted or mismatching streams are discarded.
- Ten-second connection, thirty-second read/write/pool timeouts; thirty-second
  total metadata and five-minute total artifact deadlines.
- Upstream responses/clients close on errors, deadlines, cancellation and a
  disconnected installing browser. Acquisition creates no temporary files.

The installer independently validates the complete archive and checksums, both
ZIP layers, manifest, compatibility, dependencies, ownership and immutable
identity. Its optional expected ID/version/digest arguments bind the validated
archive to the user's Store selection before staging. Remote display metadata
never replaces archive validation. Installation uses the existing world mutation
transaction; network waiting happens outside it. Once publication begins, the
existing cancellation-safe thread/transaction cleanup completes it atomically.

Dependency errors come directly from the installer. The client does not recursively
download dependencies. Shared Python preparation still happens after restart
through the existing bootstrap and its retry lifecycle. Download failure does
not alter selected versions or other local data; retry starts the artifact anew.
There is no Range/resume or background download manager.


## Client limits

The client supports catalog, details and explicit Get/Update. It does not perform
background updates, recursive dependency acquisition, publisher login or uploads.
Author submission happens in the [separate browser workflow](creator-packs.md#submit-to-marketplace).
These client limits do not describe the complete Marketplace website's features.

New passive/content Packs may activate immediately through the installer; Python
code and already loaded version changes need restart. Selected and loaded versions
remain distinct. See [distribution](pack-distribution.md#storage-and-lifecycle).

## Acceptance

Use the [existing local and fake-Store acceptance runners](developers/testing.md).
A fake HTTP result is not live Marketplace, native desktop or publication acceptance.
