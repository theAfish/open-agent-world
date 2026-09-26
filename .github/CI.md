# Pull request checks

`ci.yml` runs on every pull request, every push to `dev`, and manual dispatch.
It does not use path filters or `continue-on-error`. Repository administrators
should require these three checks in the `dev` branch ruleset after their first run:

- `Backend (Linux)`: all tests in `tests` and `backend/tests`, including migration,
  recovery, authorization, and run lifecycle regressions.
- `Frontend and deployment (Linux)`: Vitest, TypeScript plus Vite production build,
  and the existing deployment Playwright scenario against that build.
- `Core smoke (Windows)`: startup, control-plane access, deployed application,
  request context/correlation/health, durable idempotency, SQLite persistence/migration,
  storage relocation, and acceptance-helper contracts.

Python 3.12 and Node 24 match the desktop release toolchain. `uv sync --locked`
rejects backend lock drift, and `npm ci` rejects frontend manifest/lock drift.
The full backend job installs both optional adapters because existing tests import
Google ADK and LiteLLM directly; no external model credentials are needed.

The backend lock currently excludes plugin-specific test dependencies. The full
suite therefore explicitly installs the pinned PDF dependency from
`requirements-ci.txt`, with dependency resolution disabled so it cannot change
the locked backend environment. Move this pin into a shared locked development
group when plugin dependency ownership is consolidated. Tests run with the
environment's Python directly so a later `uv run` sync cannot remove the extra
dependency. `run-backend-tests.py` exposes declared local plugin sources before
pytest collection, using the same source layout as the application loader.

To reproduce the Linux backend job from the repository root:

```sh
uv sync --project backend --locked --dev --all-extras --python 3.12
uv pip install --python backend/.venv/bin/python --no-deps --only-binary :all: -r .github/requirements-ci.txt
backend/.venv/bin/python .github/run-backend-tests.py tests backend/tests
```

On Windows, use `backend/.venv/Scripts/python.exe` in place of the Unix Python path.
Frontend and deployment reproduction uses the commands already in `ci.yml`;
Playwright installs Chromium and the deployment config starts its own isolated
mock runtime with `frontend/dist`.
For an existing local Python environment, set `OAW_TEST_PYTHON` to its executable;
the deployment browser configuration otherwise uses `backend/.venv`.

Backend JUnit reports are retained for seven days, as are browser traces and
screenshots after deployment failures. Existing native sandbox and external XRD
science tests retain their explicit prerequisite skips. A passing CI run is not
evidence that privileged OS isolation, a private network, or an external science
runtime has been accepted; those require the documented runtime-specific suites.
The workflow supplies check results but does not itself enable branch protection.
