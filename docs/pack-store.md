# Pack Store Client V0

Verified implementation and platform boundaries: [acceptance report](pack-store-acceptance.md).

Open **Pack & Card Library → Store**, search for a Pack, open its details and
choose **Get**. Installation selects the version for the next OAW startup.
Restart OAW, open the Pack in **Packs**, collect its cards and add them to a deck.
Store lists an explicit **Update** action when a newer version is available;
it does not poll or install updates in the background.

The official Marketplace endpoint has not been deployed. An unconfigured build
shows **Store unavailable → Retry**, while Packs, Cards, Deck and World continue
to work. Development and acceptance can set `OPEN_AGENT_WORLD_MARKETPLACE_URL`
to the Marketplace service origin (without `/v1`). This is a host environment
override, not an ordinary Settings UI field. The single release-owned default is
`backend/official_marketplace.py:OFFICIAL_MARKETPLACE_URL`. An explicit override
wins; an explicit empty override disables Store for development/offline checks.
All URL validation and networking remain lazy, so startup does not need internet.
Tagged Desktop releases require a confirmed default. The default stays unset
until the service is deployed and verified; no guessed address is shipped.
See [production readiness and remaining operator steps](marketplace-production.md).

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

No PackSource interface was needed: both sources already converge on the same
`bytes → install` boundary. There is no second installer, registry or runtime.
Future Workshop acquisition can supply canonical bytes and expected identity to
this boundary. No Steam/provider framework has been introduced.

Dependency errors come directly from the installer. V0 does not recursively
download dependencies. Shared Python preparation still happens after restart
through the existing bootstrap and its retry lifecycle. Download failure does
not alter selected versions or other local data; retry starts the artifact anew.
There is no Range/resume or background download manager.

## Acceptance

Host integration scenarios live in `frontend/e2e/pack-host.spec.ts` because they
span installation, process restart, Library, Deck, World and frontend runtime.
The same post-restart Greeter scenario is reused for all sources. Tests preserve
the existing externally built fixture; normal tests need no private GitHub access.

```powershell
# Build fixture once if .outputs/pack-acceptance/build.json is missing:
npm.cmd --prefix frontend run build:pack-sdk
Push-Location frontend
npm.cmd pack ./pack-sdk --pack-destination ../.outputs
Pop-Location
backend/.venv/Scripts/python.exe scripts/build-external-greeter.py

npm.cmd --prefix frontend run build
node frontend/scripts/run-pack-e2e.mjs                 # local file
node frontend/scripts/run-pack-e2e.mjs --store-fake    # fake HTTP + offline
```

The fake HTTP server implements catalog/search/pagination/detail/version and
streams the fixture bytes. It is stopped before the offline UI scenario. Real
Shared Python provisioning may download the fixture's declared `colorama`
requirement from PyPI; the fake Marketplace itself never accesses GitHub.

The explicitly gated real acceptance can use an already running Marketplace:

```powershell
$env:OAW_STORE_REAL_ACCEPTANCE = '1'
$env:OPEN_AGENT_WORLD_MARKETPLACE_URL = 'http://127.0.0.1:8000'
node frontend/scripts/run-pack-e2e.mjs --store-real
```

Or launch the existing unmodified Marketplace with a copied seeded database:

```powershell
$env:OAW_STORE_REAL_ACCEPTANCE = '1'
backend/.venv/Scripts/python.exe scripts/run-store-private-acceptance.py `
  --marketplace-repo D:/AI/oaw-marketplace
```

That helper uses an existing `MARKETPLACE_GITHUB_TOKEN` or authenticated GitHub
CLI credential, sends it only to the Marketplace subprocess, and removes GitHub
and Marketplace credential variables from the OAW/browser environment. It does
not publish, upload, modify the Marketplace source or persist credentials.

The real gate requires the original `example.greeter@0.1.0` artifact, 4,191 bytes:

```text
34ee377564f6fd7776a041cc8db82d6e9f8e5bd107c4b9287f416122259cffb5
```

It checks the install database's actual digest after Get and runs the complete
restart/Library/Deck/World/persisted-interaction scenario. Each fresh profile
contains screenshots, logs and a success `receipt.json` under
`.outputs/pack-acceptance/<source>-<timestamp>/`.

To run with the formal packaged interpreter, locked dependencies, bundled code
and frontend instead of the development interpreter, build the Desktop payload,
then use `--desktop-payload .open-agent-world/desktop-payload` with the private
helper, or set `OAW_PACK_DESKTOP_PAYLOAD` for the Node runner. This exercises the
packaged launch entry with `-I -B`. It is distinct from interacting with Tauri's
WebView or installing the NSIS executable.

## Deferred

Auth/accounts, namespace ownership, publisher identity, publishing/upload APIs,
OAuth/token storage, commercial Packs, reviews, ratings, moderation, signatures,
cloud storage, Steam, automatic updates, automatic dependency installation and
hot loading remain for later stages. Marketplace V0 is unchanged by this client.
