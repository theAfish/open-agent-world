# Build and install a Greeter Card App

Build a card whose greeting can be edited by a person and used by a connected
Agent. Start with the complete, tested [Python Greeter](../../examples/plugins/greeter/README.md),
add a React editor, then install the same project as a `.oawpack`.
No model key or Sandbox is needed to build it or test its tool through the host.

## 1. Prepare a runnable card

Install Git, Python 3.11+, uv and Node.js 22+ (Node 24 matches CI). Clone OAW and
switch to `dev`. Keep your app in its own directory outside the host checkout.
The public SDK and Pack CLI currently ship in this repository; do not assume an
npm or PyPI publication exists.

Windows PowerShell, from your OAW checkout:

```powershell
$OAW = (Get-Location).Path
$PROJECT = Join-Path (Split-Path $OAW -Parent) 'my-greeter'
Copy-Item -Recurse "$OAW/examples/plugins/greeter" $PROJECT
$PYTHON = "$OAW/backend/.venv/Scripts/python.exe"
$PACK = "$OAW/scripts/oaw-pack.py"
```

Linux/macOS, from your OAW checkout:

```sh
OAW="$PWD"
PROJECT="$(dirname "$OAW")/my-greeter"
cp -R "$OAW/examples/plugins/greeter" "$PROJECT"
PYTHON="$OAW/backend/.venv/bin/python"
PACK="$OAW/scripts/oaw-pack.py"
```

Choose a fresh destination; do not copy into an existing project. You now have
`pyproject.toml`, `src/oaw_greeter_plugin/__init__.py` and `tests/`. The Python
entry point is `oaw_greeter_plugin:create_plugin`.

Create `$PROJECT/oaw.pack.toml`:

```toml
schema_version = 3

[distribution]
id = "community.greeter"
name = "Greeter"

[compatibility]
oaw = ">=0.1.0,<1"
plugin_api = "1.14"

[plugin]
owner = "community.greeter"
packs = ["community.greeter.default"]
node_types = ["community.greeter"]

[components.backend]
project = "pyproject.toml"
mode = "host-adapter"
```

The version comes from `pyproject.toml` (`0.1.0`). Distribution ID identifies the
installed archive; owner identifies the runtime plugin; Pack ID identifies its
Library collection; node type identifies saved card instances. They need not
be equal. Keep them stable after users save cards. Use your own namespace before
publishing a derivative; rename the declarations and Python registrations together.

Prepare and start a separate source-development profile from the OAW root:

```powershell
./scripts/setup.ps1 -Profile card-app -SkipBuild -PluginPath $PROJECT
./scripts/dev.ps1 -AgentRuntime mock -Profile card-app -PluginPath $PROJECT
```

```sh
bash scripts/setup.sh --profile card-app --skip-build --plugin-path "$PROJECT"
bash scripts/dev.sh --agent-runtime core.mock --profile card-app --plugin-path "$PROJECT"
```

Open the printed URL. In **Pack & Card Library → Packs**, open **Greeter**, add its
card to your deck and place it. Open it to inspect the saved configuration. This
Python-only card has a read-only fallback; the editor comes in step 4. Stop the
launcher with Ctrl+C before changing Python code or adding build dependencies.
Setup prepares dependencies; normal `dev` startup does not install them.

## 2. Understand behavior and data

Read `src/oaw_greeter_plugin/__init__.py` in your project:

| Definition | What to change |
| --- | --- |
| `GreeterConfig` | Saved `greeting`, `punctuation` and `uppercase` fields, defaults and validation |
| `NodeTypeDefinition` | Label, appearance, configuration model and lifecycle |
| `GreeterRuntime.greet` | The greeting behavior |
| `GreeterLifecycle` / `GreeterMutation` | Restore per-card runtime data at startup; commit or roll back changes |
| `PackDefinition` | Which card appears in the Library collection |

With `greeting="Welcome"` and `punctuation="!"`, greeting Ada produces
`Welcome, Ada!`; `uppercase=True` changes it to `WELCOME, ADA!`. The integration
test below updates configuration through the host and checks the runtime result.
You will add the editable interface in step 4. Changing a Python default affects
new cards; it does not overwrite configuration already saved in a World.

The host persists configuration; the runtime cache is reconstructed from it.
Do not add a second configuration database. Larger editable content belongs in
[documents](../reference/plugin-documents.md); session-scoped data belongs in
[card state](../card-state.md). Registration should not start workers, perform
network calls or mutate user files.

## 3. Connect an Agent and verify authorization

The same Python source registers:

- `CapabilityDefinition(kind="community.greeter.greet", tool_name="greet", ...)`,
  with one required `name` argument;
- a card trait `community.greeting-target`;
- `RelationshipDefinition(id="community.greet", ...)`, matching a `core.agent`
  source to that target trait, granting the greeting capability.

Place an Agent and drag a connection to Greeter. Choose **Greet with**. The handler
uses `capability.target_id`, which the host authorizes, rather than accepting an
arbitrary target ID from the model. A trait matches compatible endpoints; it
does not grant a permission by itself. This relationship exposes a tool; it is
not a typed payload pipeline or an automatic execution schedule.

Run the existing integration test from the OAW root:

```powershell
uv run --project backend --with-editable $PROJECT python -m pytest -p no:cacheprovider "$PROJECT/tests"
```

```sh
uv run --project backend --with-editable "$PROJECT" python -m pytest -p no:cacheprovider "$PROJECT/tests"
```

It checks entry-point discovery, a real connection-derived tool call, configuration
changes, isolated plugin instances, Legion cloning, invalid input and revocation
after the target is deleted. A passing direct tool test does not show that a
language model chooses the tool. To try that separately, select a real model,
connect the Agent to a Conversation with **Participate**, and ask it to greet Ada
using the connected Greeter. The mock runtime is not a language model.

For exact matching and authorization rules, see [relationships and capabilities](../reference/plugin-capabilities.md).

## 4. Add the React interface

In the existing `NodeTypeDefinition` call, immediately after
`config_model=GreeterConfig,`, add:

```python
frontend={"body": "greeting"},
```

Append this table to `oaw.pack.toml`:

```toml
[components.frontend]
entry = "frontend/index.tsx"
project = "package.json"
mode = "pack-build"
```

Create `package.json` at the project root:

```json
{
  "name": "my-greeter",
  "private": true,
  "type": "module",
  "scripts": { "build": "node build.mjs" },
  "devDependencies": {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "@types/react": "^18.3.0"
  }
}
```

Create `frontend/index.tsx`:

```tsx
import { useState } from 'react';
import type { FrontendPlugin, PluginViewProps } from '@oaw/plugin-api';

function Greeting({ card, host }: PluginViewProps) {
  const [greeting, setGreeting] = useState(String(card.config.greeting ?? 'Hello'));
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      await host.updateConfig({ greeting });
      setStatus('Saved');
    } catch (error) {
      setStatus(String(error));
    } finally {
      setSaving(false);
    }
  }
  return <div className="nodrag nopan" style={{ padding: 16 }}>
    <label>Greeting<input aria-label="Greeting" value={greeting}
      onChange={event => setGreeting(event.target.value)} /></label>
    <button disabled={saving} onClick={() => void save()}>Save greeting</button>
    <p role="status">{status}</p>
  </div>;
}

export default { apiVersion: 1, views: { greeting: Greeting } } satisfies FrontendPlugin;
```

Create `build.mjs`:

```js
import { buildPackFrontend } from '@oaw/plugin-api/build';
await buildPackFrontend({ entryPoint: 'frontend/index.tsx', outfile: 'dist/frontend/index.js' });
```

Build and pack the host's SDK, then install the tarball into your external project:

```powershell
npm.cmd --prefix "$OAW/frontend" run build:pack-sdk
npm.cmd pack "$OAW/frontend/pack-sdk" --pack-destination $PROJECT
npm.cmd --prefix $PROJECT install --save-dev "$PROJECT/oaw-plugin-api-1.0.0.tgz"
npm.cmd --prefix $PROJECT run build
```

```sh
npm --prefix "$OAW/frontend" run build:pack-sdk
npm pack "$OAW/frontend/pack-sdk" --pack-destination "$PROJECT"
npm --prefix "$PROJECT" install --save-dev "$PROJECT/oaw-plugin-api-1.0.0.tgz"
npm --prefix "$PROJECT" run build
```

Keep the generated lockfile and the SDK tarball available to your build, or use a
versioned package once you have a registry that actually publishes it. Re-run
setup with the same profile and project after adding frontend dependencies, then
restart dev. Open Greeter, enter `Welcome`, choose **Save greeting**
and reopen it. The value should persist and the tool should use it.

The helper shares the host's React and SDK. Do not bundle a second React, mount
a second application root or import host source paths. `body` replaces this card's inspector content. `settings` is the configuration slot inside shared Agent
controls; it is not the editor slot for an ordinary card. For CSS,
assets, workers and multi-file builds, use the [frontend SDK](../../frontend/pack-sdk/README.md).

## 5. Package the same project

Build the Python wheel, then package it with the already-built frontend:

```powershell
uv build $PROJECT --wheel --out-dir "$PROJECT/dist/backend"
& $PYTHON $PACK doctor $PROJECT
& $PYTHON $PACK build-project $PROJECT "$PROJECT/dist/greeter-0.1.0.oawpack" --backend-wheel "$PROJECT/dist/backend/open_agent_world_plugin_greeter-0.1.0-py3-none-any.whl" --frontend-dir "$PROJECT/dist/frontend"
& $PYTHON $PACK inspect "$PROJECT/dist/greeter-0.1.0.oawpack"
```

```sh
uv build "$PROJECT" --wheel --out-dir "$PROJECT/dist/backend"
"$PYTHON" "$PACK" doctor "$PROJECT"
"$PYTHON" "$PACK" build-project "$PROJECT" "$PROJECT/dist/greeter-0.1.0.oawpack" --backend-wheel "$PROJECT/dist/backend/open_agent_world_plugin_greeter-0.1.0-py3-none-any.whl" --frontend-dir "$PROJECT/dist/frontend"
"$PYTHON" "$PACK" inspect "$PROJECT/dist/greeter-0.1.0.oawpack"
```

`doctor` inspects declarations; it does not execute the card. `build-project`
packages prebuilt artifacts; it does not run npm or compile Python. It refuses to
overwrite an existing output. Use a new output path when rebuilding locally;
increase the project and descriptor version together for a published update.

Greeter only needs the host's compatible Pydantic dependency. Additional adapter
libraries need verified wheels, resolved through `resolve-adapter` and supplied
with `--dependency-wheels`; see [Pack dependencies](../pack-distribution.md).
The archive must include every frontend output listed by its build manifest.

## 6. Install and check in the host

Use a clean test profile so an editable/source copy cannot mask a broken archive.
Build the host frontend from the OAW root with `npm --prefix frontend run build`.
Choose a new data directory and an unused port:

```powershell
$env:OPEN_AGENT_WORLD_DATA_ROOT = "$PROJECT/host-test"
& $PYTHON -m backend.launcher --mode production --port 38583 --strict-port --frontend "$OAW/frontend/dist"
```

```sh
OPEN_AGENT_WORLD_DATA_ROOT="$PROJECT/host-test" "$PYTHON" -m backend.launcher --mode production --port 38583 --strict-port --frontend "$OAW/frontend/dist"
```

In that host, use **Library → Packs → Install Pack from File...** to install
`dist/greeter-0.1.0.oawpack`. Review the plan and confirm. Python code requires a
restart: stop and relaunch the same command with the same data directory.

Open Greeter, add its card to a deck, place it and open it. Save a
greeting, reload and verify it persists. Connect an Agent with **Greet with**;
check the tool using the integration approach above or a configured model.
Remove the connection and confirm the Agent no longer has that access.
Do not treat a successful build or archive inspection as host acceptance.

For repeatable archive tests, use the existing installer fixtures in
[testing](testing.md). The separate [packaged Greeter fixture](../../examples/packs/greeter/README.md)
is used by host browser CI; it has a different identity and does not replace the
authorization test for this app.

To distribute, share the `.oawpack` and its requirements. Marketplace submission
is a separate browser workflow with author authorization and review; building an
archive does not publish it. See [submission](../creator-packs.md#submit-to-marketplace).
