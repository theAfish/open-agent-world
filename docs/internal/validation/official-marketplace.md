# Official Marketplace integration — 2026-10-23

> Historical record, not current product or deployment instructions. Verify against the current source before use.


OAW now defaults to `https://open-agent-world.space`. This is the service origin,
without `/marketplace` or `/v1`. The source base was latest `origin/dev`,
`0464d0adb6f1591ca696e1a274681c8cbec2bf31`, verified by fetching `dev`.

Production references supplied for this acceptance:

- Marketplace backend: `7b42ba3cc74f3f33a922586a3ca49ccd4da737ce`.
- Website: `dc5c74695323d310f8e3eb860890ed6c921e6602`.
- Published Pack: `example.greeter@0.2.0`, Schema 3, 4,463 bytes.
- SHA-256: `c589f96c0201c35da3398a747e47b845bfb2d7d3dac3b8ff5c2c427cdbd6ad41`.

The production references are operator-provided deployment provenance; OAW's
anonymous acceptance verifies the public service and artifact, not server Git
checkouts. No Marketplace infrastructure, database, website or Release was changed.

## Implementation

Only the release-owned endpoint changed in application runtime code. Settings
still honors a nonempty development override and treats an explicitly empty
override as Store-disabled. URL validation and HTTP clients remain lazy. Store
downloads still enter the existing Pack installer and its integrity checks.
No startup probe, second installer, Schema 1 support or Store UI redesign was added.

The existing official runner now records the accepted version and restarts its
host with unreachable process-local HTTP proxies after online acceptance. This
keeps the official endpoint and absent override while exercising cold startup,
persisted state and Store failure. The offline UI test waits for the existing GET
retry sequence, including a new Loading state after clicking Retry, before
checking the final error. It then interacts with Greeter and reloads the saved
offline greeting. Nothing changes the machine's network settings.

## Validation

Public HTTPS catalog, package details and explicit version metadata returned 200
without redirects. The manifest is Schema 3 and metadata matches the digest above.
The existing runner rejected both empty and nonempty
`OPEN_AGENT_WORLD_MARKETPLACE_URL` values before launching a host. All successful
official runs had the variable absent, not merely empty.

Windows source-host acceptance passed all three phases in a fresh profile:

1. Store discovery/search, package details, Get/download, installed digest and
   pending-restart status; no frontend calls to the remote `/v1` API.
2. Process restart, backend and runtime frontend activation, Pack opening,
   Card Library, Deck, World placement, Greet, API state and browser reload.
3. Another process restart with upstream HTTP blocked, saved greeting and Deck,
   Store unavailable and completed Retry, local Packs/Cards, another Greet and
   reload with `Hello, Offline World!` retained.

Source success receipt and screenshots:
`.outputs/pack-acceptance/store-official-1792746744544/`.
The browser was Windows Microsoft Edge through Playwright, with OAW production
mode and mock Agent runtime; this does not validate a live model provider.

Focused backend tests: **114 passed** across `test_official_marketplace.py`,
`test_store.py`, `test_marketplace.py`, `test_desktop_release_config.py` and
`test_release_quality.py`. This includes no Marketplace I/O during offline
startup/local APIs, failure isolation, invalid/empty configuration, malformed and
interrupted downloads, digest rejection, and independent release safety policies.
The production frontend build and endpoint/version release checks passed.
Strict documentation validation passed: 16 tests, 82 generated pages and 9,766
local links/assets checked.
This was not a full cross-project test run.

## Desktop boundary

The actual Windows x64 payload was built with `scripts/package-backend.py`,
using its `--cache-dir` option for the existing uv cache. The host dependencies
remain locked and hash-checked. The payload contains Python 3.12.4, the built
production frontend and the source endpoint byte-for-byte. Its built-in self-test
passed with 14 plugins (Core plus 13 bundled Packs), Sandbox Python ready, and
backup/restore OK, including the self-test's Windows credential encryption check.
Build timestamp: `2026-10-23T09:18:02.329040+00:00`.

Packaged-host production acceptance passed the same three phases above, using
the bundled interpreter with `-I -B`, `launch.py`, packaged dependencies and
packaged frontend. The accepted digest is exactly the published digest above;
the receipt records `marketplaceOverride: false` and `offlineRestart: true`.
Packaged success receipt, screenshots and host logs:
`.outputs/pack-acceptance/store-official-1792747429093/`.
Both successful host runs started and cleanly stopped three backend processes.
Their offline phases each received six Store 503 responses (initial request and
Retry, with the existing two retries per GET) while local interaction remained
usable. Screenshots for Store, details, World and offline behavior were inspected.
A focused packaged offline rerun also passed after adding a wait for inspector
opacity to reach 1 before the post-reload screenshot. DOM text/visibility checks
alone could capture the card body's transparent transition frame. A separate
offline cold-start/reload visual probe confirmed the saved greeting as well;
its evidence is in `.outputs/official-marketplace/inspect-reloaded.png` and
`payload-offline-visual.log`. No application rendering code changed.

Native build preflight (`npm --prefix desktop run build -- --no-bundle`) failed
because `cargo metadata` could not run: Cargo/Rust is absent from PATH and the
checked conventional user/repository tool locations. No native executable was
available in this checkout. No new NSIS installer was built or installed, and
no Tauri/WebView2 interaction is claimed. No macOS host was available.

## Release readiness

`scripts/check-marketplace-release.py` passes for the embedded origin. Its
existing workflow condition remains pushed tags plus
`vars.OAW_REQUIRE_MARKETPLACE == 'true'`; manual preview builds are unaffected.
The repository variable was inspected and is currently **unset**. Set it to
`true` before a public release that promises Store access. This gate validates
configuration only; repeat real service acceptance against each release payload.
Signing, updater settings, exact-commit CI, ancestry, version and draft-release
checks were preserved. No tag, release or author publishing workflow was created.

Before public release:

- Merge the integration and obtain the required CI checks on the release SHA.
- Prepare a new consistent application version; source metadata is still 0.1.0
  and historical releases through v0.5.0 must not be reused.
- Enable the Marketplace release variable and repeat production acceptance.
- Build preview installers on Windows x64 and both macOS architectures; install
  them and complete native Store, restart, offline, backup/restore and upgrade
  acceptance. Payload/browser evidence does not replace this.
- Complete applicable platform signing/notarization and updater acceptance from
  [the release guide](../../releasing.md) before manually publishing a release.

Local logs and endpoint/environment evidence are under
`.outputs/official-marketplace/`; fresh acceptance profiles retain screenshots,
host logs and success receipts. The first source offline attempt exposed a
10-second test wait shorter than Windows connection failure plus GET retries;
the completed run preserves that production behavior. The initial payload
download was slow; the cache rebuild retains lockfile hashes and uses the
existing packaging command's `--cache-dir` option.
Observed nonfatal build warnings include Vite chunk sizing, the existing
Starlette TestClient deprecation, a MatCreator Requests character-detection
warning and LiteLLM's remote cost-map timeout with local fallback. The Store
acceptance does not establish end-to-end model or scientific Pack functionality.
