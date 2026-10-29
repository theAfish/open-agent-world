# External Greeter Pack

This standalone Schema 3 Python-backed example declares its distribution in
`oaw.pack.toml` and imports only `open_agent_world.plugin_api` and `@oaw/plugin-api`.
Copy it outside OAW before building; no host source aliases are required.

1. Obtain the frontend SDK tarball (`npm run build:pack-sdk`, then
   `npm pack ./pack-sdk` in OAW's `frontend/`).
2. Run `npm install /path/to/oaw-plugin-api-1.0.0.tgz` and `npm run build`.
3. Run `uv build --wheel --out-dir dist/backend`.
4. With OAW's Python interpreter, package and inspect:

```sh
python /absolute/path/to/OAW/scripts/oaw-pack.py build-project /path/to/greeter /path/to/greeter-0.2.0.oawpack \
  --backend-wheel /path/to/greeter/dist/backend/oaw_pack_example_greeter-0.2.0-py3-none-any.whl \
  --frontend-dir /path/to/greeter/dist/frontend
python /absolute/path/to/OAW/scripts/oaw-pack.py inspect /path/to/greeter-0.2.0.oawpack
```

Install from the Pack Library, restart OAW, add Greeter to your deck and use
**Greet**. The existing distribution, runtime owner and card IDs are preserved;
Schema 1 archives are unsupported. Use the [Card App tutorial](../../../docs/developers/card-app.md)
for a separate example that also grants an Agent capability.

`scripts/build-external-greeter.py` performs this build in an external temporary
directory. `npm run test:e2e:packs` validates installation, restart, frontend
loading and persisted greetings. The fixture needs no Sandbox packages.

## Progressive help

This example requires Plugin API 1.26 and declares a two-step card tutorial plus Pack reference documentation. Definitions live in the Python entrypoint and are delivered through the catalog; the frontend needs no tutorial code. See [the authoring guide](../../../docs/developers/progressive-tutorials.md).
