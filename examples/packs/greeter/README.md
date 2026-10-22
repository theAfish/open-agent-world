# External Greeter Pack

This directory is a standalone repository fixture. Copy it outside OAW before
building it. It imports only `open_agent_world.plugin_api` and `@oaw/plugin-api`;
there are no host source paths or bundler aliases into the OAW checkout.

1. Obtain the local frontend SDK tarball (the OAW host maintainer runs
   `npm run build:pack-sdk` then `npm pack ./pack-sdk` in `frontend/`).
2. `npm install /path/to/oaw-plugin-api-1.0.0.tgz`
3. `npm run build`
4. `uv build --wheel --out-dir dist/backend`
5. With the host Python distribution, run
   `python -m open_agent_world.pack build /path/to/greeter/dist /path/to/greeter-0.1.0.oawpack`.
6. Open OAW's Pack Library, choose **Install Pack from File...**, inspect the
   package and install it. Restart OAW, wait for Sandbox runtime **Ready**,
   open Greeter, add its card to your deck, place it and click **Greet**.

The declared `colorama==0.4.6` dependency is provisioned by the existing shared
Sandbox Python runtime; it is deliberately not imported into the host backend.

Host CI uses `scripts/build-external-greeter.py --host-only` to omit this unused
runtime example from the external copy's manifest. It still builds the real
SDK frontend and Python adapter, installs the archive, restarts the host, and
exercises the same card and persistence assertions. The source fixture remains
unchanged. Run without the flag on a machine with a working native Sandbox to
also verify shared Python preparation; host-only CI is not proof of that feature.

## Progressive help

This example requires Plugin API 1.26 and declares a two-step card tutorial plus Pack reference documentation. Definitions live in the Python entrypoint and are delivered through the catalog; the frontend needs no tutorial code. See [the authoring guide](../../../docs/developers/progressive-tutorials.md).
