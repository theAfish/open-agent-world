# Test and distribute

For an independently installable frontend + backend artifact, follow
[Local Pack distribution](../pack-distribution.md) and the
[external Greeter fixture](../../examples/packs/greeter/README.md). The wheel-only
instructions below remain useful for source-checkout development.

## Test the first-card package

From the repository root:

```sh
uv run --project backend --with-editable ./examples/plugins/hello_world python -m pytest -p no:cacheprovider examples/plugins/hello_world/tests
```

The [test](../../examples/plugins/hello_world/tests/test_hello.py) loads real entry-point metadata, checks the pack and card owner, creates and edits a card, closes the host, and verifies its configuration after reopening the same store. For a renamed copy, change the editable path, test path, and expected IDs to match your package.

## Choose meaningful checks

| Your plugin adds… | Verify |
| --- | --- |
| Configuration | Valid defaults, rejected invalid input, persistence after restart |
| Agent tools | Authorized calls and revocation after the connection or resource is removed |
| Managed resources | Create/update rollback, restart recovery, deletion cleanup |
| Frontend views | Save failures, reload, unavailable views, and production build |
| Templates | Clone behavior, remapped IDs, and exclusion of private bindings |

Keep plugin tests with the package. Host integration tests belong in the host suite when they exercise multiple parts of OAW. See [Greeter's integration test](../../examples/plugins/greeter/tests/test_greeter_plugin.py) for a complete tool path.

For a custom frontend, run the production build and exercise the view in OAW. Browser interaction tests, backend tests, real model trials, and native Sandbox tests cover different things; report which ones you actually ran.

## Build a Python package

For the included example:

```sh
uv build ./examples/plugins/hello_world --out-dir .tmp/hello-dist
```

For your own package, replace the input path. Include any declared package resources in the wheel. Check the built artifact in a clean compatible OAW checkout before sharing it.

Source development and publication use the same `oaw.pack.toml` declaration:

- Put the source package directly in `plugins/` and prepare its declared adapter
  dependencies with the backend interpreter running
  `scripts/install-plugins.py plugins/hello_world`. Startup itself never installs.
- Build the wheel and any frontend bundle, then use
  `python -m open_agent_world.pack build-project` to produce `.oawpack`. Include
  additional adapter wheels resolved with `resolve-adapter`. Install that artifact
  through the Pack Library or Store and restart OAW.

See [the complete author workflow](../pack-distribution.md). A bare Python wheel
is a build input, not a complete Pack distribution. Test installation from the
artifact in a clean profile to catch dependencies hidden by a developer environment.

## Write the plugin README

Include its purpose, compatible OAW/API version, dependencies, installation path, pack/card names, the connections users should create, and a short working example. Explain storage, external services, and removal steps when relevant.

Users must remove active uses before uninstalling through the Pack Library. The
host checks references and reclaims only unused managed dependencies. Registration
must not create resources as a side effect. Do not include credentials, generated
user data, or private machine paths in a distribution.

Check licensing before publishing: OAW currently has no repository license file. A package manifest alone does not establish redistribution rights for code you copy.
