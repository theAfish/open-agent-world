# Test and distribute a Card App

Complete the [Card App tutorial](card-app.md) first. Its direct integration test
checks behavior and permission revocation; its installed-host checklist checks
the artifact that recipients actually load.

## Choose tests for your boundary

| Changed behavior | Check |
| --- | --- |
| Configuration and tools | Defaults, invalid input, authorized call and revoked access |
| Documents/state | Persistence after restart, revision conflict and session isolation |
| Frontend view | Save failure, saved value after reload and missing capability handling |
| Archive | Build outside the host, inspect identity/dependencies, install in a clean profile |
| Native execution | Run on the actual target runtime; a skipped native test is not acceptance |

Use the existing [Greeter integration test](../../examples/plugins/greeter/tests/test_greeter_plugin.py)
and host fixtures. Do not duplicate every assertion across unit, service and browser
layers. Mock-runtime checks do not verify real model selection or output quality.

## Reproduce the existing packaged example

From the OAW root, after preparing its backend and frontend dependencies:

```sh
npm --prefix frontend run build:pack-sdk
npm pack ./frontend/pack-sdk --pack-destination .outputs
```

Create `.outputs` first if it does not exist. Run
`scripts/build-external-greeter.py` with `backend/.venv/Scripts/python.exe` on
Windows or `backend/.venv/bin/python` on Linux/macOS. It copies
`examples/packs/greeter` outside the host, builds the wheel/frontend and writes
`.outputs/pack-acceptance/build.json`.

```sh
npm --prefix frontend run build
node frontend/scripts/run-pack-e2e.mjs
node frontend/scripts/run-pack-e2e.mjs --store-fake
```

These runners need the configured Playwright browser. They install through the
local host, restart Python, collect/place a card and check its saved UI behavior.
The fake Store runner also checks unavailable-Store behavior. They do not publish
a Pack or prove a native installed desktop app or a live Marketplace service.
Use an unused `OAW_PACK_E2E_PORT` when other hosts are running.

## Prepare the release

Use a stable distribution ID and increase the version for a changed artifact.
Build the exact artifact you tested; include its host compatibility, required
Packs and execution environments in the README. Schema 3 is for code/declarative
projects; Schema 2 is for code-free content exports. Schema 1 is rejected.

Share the `.oawpack`, or follow the separate [Marketplace submission](../creator-packs.md#submit-to-marketplace)
workflow. Submitting enters review; it is not an immediate publication.
