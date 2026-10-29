# Python Greeter example

This complete Python example provides saved greeting settings, transactional
runtime state and the **Greet with** relationship. Its Agent tool uses a target
authorized by the host; deleting the edge or target revokes subsequent calls.

Follow [Build and install a Greeter Card App](../../../docs/developers/card-app.md)
([中文](../../../docs/developers/card-app.zh-CN.md)) to copy this project, add its
source manifest and React settings view, build `.oawpack` and install it. This
folder is the Python starting point, not an already packaged Card App.

The [integration test](tests/test_greeter_plugin.py) covers real entry-point discovery,
configuration changes, separate instances, Legion cloning, invalid arguments and
permission revocation. From the OAW root:

```sh
uv run --project backend --with-editable ./examples/plugins/greeter python -m pytest -p no:cacheprovider examples/plugins/greeter/tests
```

This test invokes the tool through the host without a language model. See the
[API directory](../../../docs/plugins.md) for the exact contracts.
