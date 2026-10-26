# Pack Store Client V0

Verified implementation and platform boundaries: [acceptance report](internal/validation/pack-store.md).

Open **Pack & Card Library → Store**, search for a Pack, open its details and
choose **Get**. Installation selects the version for the next OAW startup.
Restart OAW, open the Pack in **Packs**, collect its cards and add them to a deck.
Store lists an explicit **Update** action when a newer version is available;
it does not poll or install updates in the background.

OAW connects to the deployed official Marketplace at
`https://open-agent-world.space` by default; users do not configure an endpoint.
If it is unreachable, **Store unavailable → Retry** leaves Packs, Cards, Deck and
World usable. Development can set `OPEN_AGENT_WORLD_MARKETPLACE_URL` to a service
origin (without `/marketplace` or `/v1`). This is a host environment override,
not an ordinary Settings UI field. The single release-owned default is
`backend/official_marketplace.py:OFFICIAL_MARKETPLACE_URL`. An explicit override
wins; an explicit empty override disables Store for development/offline checks.
All URL validation and networking remain lazy, so startup does not need internet.
Pushed Desktop release tags check the default when `OAW_REQUIRE_MARKETPLACE=true`;
manual preview builds retain the existing release policy.
See [production integration acceptance](internal/validation/official-marketplace.md).

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
streams the Schema 3 fixture bytes. It is stopped before the offline UI scenario.
Greeter has no Sandbox requirements; the fake Marketplace never accesses GitHub.

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

The real gate requires the same Schema 3 Greeter version and bytes recorded by
`scripts/build-external-greeter.py` in `.outputs/pack-acceptance/build.json` to be
published separately. Historical Schema 1 releases are unsupported. Publishing
or deleting Releases is an explicit operator action, never performed by this test.
Official production acceptance uses the published Schema 3 Greeter directly,
without building a local fixture or supplying any Marketplace override:

```powershell
Remove-Item Env:OPEN_AGENT_WORLD_MARKETPLACE_URL -ErrorAction SilentlyContinue
$env:OAW_PACK_VERSION = '0.2.0'
$env:OAW_PACK_EXPECTED_SHA = 'c589f96c0201c35da3398a747e47b845bfb2d7d3dac3b8ff5c2c427cdbd6ad41'
node frontend/scripts/run-pack-e2e.mjs --store-official
```

The official runner rejects even an explicitly empty override. After the online
flow it restarts the host with unreachable HTTP proxies, verifies the saved
World/Deck and local interaction, and checks Store failure and Retry. Proxy
settings apply only to the isolated acceptance host; the official origin stays
unchanged. This models upstream network failure, not an OS-wide network outage.

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
