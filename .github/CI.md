# Pull request checks

Workflow aggregate contexts are `CI required`, `Docs required`, and `Native required`.
They run without path filters on PRs to and pushes to `dev`/`main`, and on manual
dispatch. Each aggregate fails unless every dependency succeeds, including all
matrix targets. Whether they are required for a particular PR is controlled by live GitHub
rulesets. See [repository settings](../docs/contributing/index.md#repository-administrator-setup);
repository code cannot establish their current enforcement.

Frontend CI builds first, using the existing preparation step to install each
Pack's declared dependencies in its own directory. Source-component tests then
resolve Pack dependencies without adding them to the host manifest. The same job
packs the public SDK, builds the existing Greeter outside the checkout, installs
its `.oawpack`, restarts the production host, and checks cards and frontend assets
in Chromium with the mock runtime. The fixture uses `oaw.pack.toml` and the
canonical Schema 3 `build-project` toolchain. CI checks host loading and saved
card behavior; this fixture has no Sandbox dependency. No Marketplace or
model credentials are used.

`Application version consistency` checks all committed application declarations.
Desktop release requires successful latest runs of all three validation workflows
on the exact source SHA, then pins every builder to that SHA. Manual preview
builds have the same gate. See [releasing](../docs/releasing.md).

`ci.yml` extends the existing workflow on PRs targeting `dev` or `main`, pushes
to those branches, and manual dispatch. Check names already used by branch
rules remain stable; there are no path filters, blanket skips, or
`continue-on-error` bypasses.

- `Backend tests (1/4)` through `(4/4)`: all tests in `tests` and `backend/tests`,
  including public SDK contracts, migrations, recovery, authorization, and run
  lifecycle tests. Pytest collects the complete suite on each runner, then assigns
  sorted test files round-robin. Every file runs once, with its fixtures and test
  order intact. Separate runners isolate plugin preparation and subprocesses.
  `CI required` waits for all four shards; a failure does not cancel the others.
  The Core-only installation smoke runs once, on shard 1.
- `Frontend tests and build`: Vitest, TypeScript, Vite production build, and
  the deployment Playwright scenario plus shared-control, theme and popup
  acceptance in Chromium and WebKit against that build.
- `Core smoke (Windows)`: startup, authentication, deployment, request
  context/correlation/health, idempotency, persistence, migration and storage.

Python 3.12 and Node 24 match the desktop release toolchain. `uv sync --locked`
and `npm ci` reject manifest/lock drift. The full backend job retains the
existing installation of all repository plugin packages, which own their
extra dependencies, and installs both optional provider adapters. It needs
no external model credentials. `run-backend-tests.py` makes plugin sources
available during collection, including when using an existing local Python
environment instead of editable plugin installations.

Run the full backend suite from the repository root after installing the
locked backend environment and the repository plugins:

```sh
backend/.venv/bin/python .github/run-backend-tests.py tests backend/tests
```

To reproduce a CI shard, append `--shard 1/4` (or `2/4`, `3/4`, `4/4`). Omitting
the option runs the complete suite. Each shard uploads its own JUnit report and
prints its 20 slowest tests. Sharding reduces elapsed time, not test coverage;
total runner minutes can increase slightly because collection and setup repeat.

On Windows use `backend/.venv/Scripts/python.exe`. The frontend commands are
`npm --prefix frontend test`, `npm --prefix frontend run build`, and
`npm --prefix frontend run test:e2e:deployment` and
`npm --prefix frontend run test:e2e:ui` after installing Playwright's Chromium
and WebKit browsers. The deployment configuration starts its own isolated mock runtime
and uses `frontend/dist`; `OAW_TEST_PYTHON` can select an existing interpreter.
UI acceptance starts a separate preview profile with Sandbox execution disabled.
WebKit checks do not replace verification of the installed macOS WKWebView.

Backend JUnit reports and failed browser traces/screenshots are retained for
seven days. Existing platform, native isolation and external science tests
retain their documented prerequisite skips. These checks do not establish
native Sandbox acceptance, real model/network acceptance or multi-user
enterprise readiness. The workflow does not configure branch protection;
repository administrators can require its checks in the branch ruleset.

## Documentation

`docs.yml` checks PRs to and pushes to `dev`/`main` without path filters:
removing a source file can break a documentation link even in a code-only PR.
Its `build` job installs only `scripts/docs-requirements.txt` and runs
`python scripts/docs.py check`. This validates source inventory, heading/language
rules, repository links, the strict MkDocs build, and generated links/search.

The separate `Documentation tutorial examples` job uses locked backend test
dependencies and installs the Hello World and Greeter entry points. Pages deploys
only after both jobs pass on `dev`, outside pull requests. Build success and
deployment success remain separate results.

Local setup and the identical validation command are in the
[documentation maintenance guide](../docs/developers/documentation.md).
