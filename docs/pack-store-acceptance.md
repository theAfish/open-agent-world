# Pack Store Client V0 implementation and acceptance

Implementation based on `dev` at `e91569b`, verified against `origin/dev` on
2026-09-22. Changes are confined to `open-agent-world`; the existing Marketplace
source and database were not modified. No publishing or account functionality
was added.

## Implementation report

1. **MarketplaceClient boundary:** `backend/packs/marketplace.py` implements
   only public HTTP catalog/detail/version/download operations, DTO validation
   and transport checks. It owns no registry, Library or runtime behavior.
2. **Host Store API:** `/api/store/packs`, Pack detail, version list/detail and
   explicit version installation are registered on the existing local router.
   Installation uses the existing management header and host authorization.
3. **Frontend:** the former Store placeholder now provides search, cursor
   pagination, detail requirements, Get/Update, installed/restart state and Retry.
   It follows the Library theme and supports English/Chinese. Local file
   installation remains in Packs. Requests cancel and stale results are ignored
   when searches change or the user leaves Store.
4. **Remote installation:** fresh explicit version metadata identifies the
   artifact; verified bytes enter `PackInstallationManager.install`. Optional
   expected ID/version/digest arguments bind its independent archive validation
   to the requested version before staging.
5. **Integrity:** the existing 128 MiB bound, exact MIME, Content-Length, digest
   header, actual byte count and locally calculated SHA are checked. Redirects
   and encodings are rejected. Connect/read/total deadlines, stream interruption
   and browser cancellation close upstream resources and discard acquired data.
6. **One installation lifecycle:** local and remote bytes use the same immutable
   version storage, archive checks, ownership, dependency validation, selection,
   rollback, restart and environment bootstrap. Network waiting occurs outside
   the world mutation lock. Retained versions use the existing activate method.
7. **No PackSource abstraction:** the existing byte-oriented installer plus
   expected identity already provides the required boundary without duplicated
   installation code. No provider discovery or registration was needed.
8. **Failure isolation:** configuration and network access are lazy. Store
   failures return short errors without upstream bodies, private storage IDs,
   repository information or credentials. Existing local features remain usable.
9. **Version decisions:** backend PEP 440 comparison produces display/action
   state; `0.10.0` correctly follows `0.9.0`. A locally newer version stays
   Installed. No automatic updates or polling. Dependency resolution remains in
   the existing installer/bootstrap; no recursive dependency downloads.
10. **Fake Marketplace:** a local HTTP fixture uses the existing external
    Greeter artifact, including incremental body transfer. Browser acceptance
    checks search, 23-item pagination, detail, Get, restart, actual interaction,
    persistence and continued local use after the fake server stops.
11. **Real private Release:** the gated helper starts the unmodified Marketplace
    against a SQLite backup of its existing seeded catalog. Only that service
    receives the existing credential. The anonymous OAW consumer installs
    `example.greeter@0.1.0` and verifies the actual installation digest.
12. **Desktop:** the formal Windows x64 payload is rebuilt by the existing
    package script, including the standalone CPython 3.12.4 interpreter, 83 locked
    dependencies, 10 bundled plugins, frontend and uv. Self-test passed, including
    Sandbox Python. Its Store modules and frontend match the current checkout.
    Its real Store Get/restart/Greeter browser acceptance also passed, using
    the packaged interpreter and bundled uv. NSIS generation was attempted but
    Tauri could not find `cargo`. Native
    WebView/installer interaction remains unverified; see the result table.
13. **Future Workshop:** acquisition can later produce canonical bytes for the
    same installer. No Steam code, source framework, second registry/runtime or
    hot loading was introduced.
14. **Next stage:** official endpoint deployment, auth, accounts, namespace
    ownership, publisher identity, publishing/upload and moderation remain
    separate work. Ratings, payments, signatures, automatic updates and cloud
    artifact storage are also outside this change.

## Verified results

| Check | Result | Evidence |
| --- | --- | --- |
| Client, Store, local installer and host regressions | 171 passed | Nine focused backend files; includes shared Python, plugin registry/loader/assets, Card Library and application launch |
| Store/Library/runtime/i18n frontend tests | 47 passed | Nine Vitest files |
| Deck/palette regressions | 14 passed | Five Vitest files |
| Production frontend | Passed | TypeScript and Vite build |
| Local file production-host acceptance | 2 phases passed | `.outputs/pack-acceptance/local-1790054130884/receipt.json` |
| Fake Store production-host acceptance | 3 phases passed | `.outputs/pack-acceptance/store-fake-1790053848020/receipt.json` |
| Real Store production-host acceptance | 2 phases passed | `.outputs/pack-acceptance/store-real-1790054005177/receipt.json` |
| Formal Desktop payload build/self-test | Passed | `.open-agent-world/desktop-payload/build-info.json` |
| Desktop payload + real Store | 2 phases passed | `.outputs/pack-acceptance/store-real-1790054981732/receipt.json` |
| NSIS installer / native WebView | Not verified | Tauri `cargo metadata` failed: program not found; Windows Computer Use runtime unavailable |
| Other platforms | Not tested | macOS Intel/Apple Silicon and Linux |

Each browser acceptance starts with a fresh profile and reuses the same
post-restart Greeter scene: Pack opening → Card Library → Deck → World → Greet →
reload with the saved greeting intact. Screenshots were inspected for catalog
and detail layout. Browser automation used independent Playwright because the
in-app Browser reported an unavailable privileged bridge / untrusted client.
These are focused acceptance results, not a claim of a full repository suite or
native desktop interaction.

The installed artifact digest in the real run is:

```text
34ee377564f6fd7776a041cc8db82d6e9f8e5bd107c4b9287f416122259cffb5
```

The original artifact is 4,191 bytes. Marketplace HTTP logs for the source-host
run are under `.outputs/store-private/1790054003037759200/`; the Desktop payload
run uses `.outputs/store-private/1790054980013217800/`. The service is stopped
after acceptance. The official endpoint is still undeployed, so normal builds
remain Store-unavailable until an endpoint is configured by the host.

Transient validation issues were resolved: restricted test networking initially
prevented PyPI provisioning, and the final pytest pass needed a workspace-local
temporary directory. The successful runs used normal authorized networking and
fresh isolated profiles. Packaging reuses a copy of completed wheel caches with
`--require-hashes`; dependency versions and lockfiles were not changed. Existing
Starlette TestClient deprecation and Vite large-chunk warnings remain.

Commands and architecture details: [Pack Store Client V0](pack-store.md).
