# Contribute to OAW

Use this route when changing the host, its documentation, or a release. To build
an independently installed extension, start with [Build a plugin](../developers/index.md).

## Follow a change through the host

1. [Run from source](../getting-started.md) and choose an isolated development profile.
2. Read [Architecture](../architecture.md) to find the component that owns the behavior.
3. Use the [technical reference](../reference/index.md) for the contract you are changing.
4. Run the relevant tests and the checks in [the CI guide](../../.github/CI.md).
5. Update the affected guide or reference using the [documentation checklist](docs-checklist.md).

## Choose a maintenance task

| Task | Start here | Continue with |
| --- | --- | --- |
| Change canvas behavior | [Canvas control](../canvas-control.md) | [Placement](../layout.md), [performance](../canvas-performance.md), [terrain](../terrain-webgl.md) |
| Change first-use guidance | [Tutorial implementation](../tutorial.md) | [First task and recovery](../product-first-use.md) |
| Change runtime boundaries | [Architecture](../architecture.md) | [Security](../security.md), [enterprise foundations](../enterprise-foundations.md) |
| Package the desktop app | [Desktop development](../desktop.md) | [Release procedure](../releasing.md) |
| Publish a workspace | [Workspace deployment](../deployment.md) | [Plugin deployment contract](../plugin-deployment.md) |
| Write documentation | [Writing checklist](docs-checklist.md) | [Preview, validate, and publish](../developers/documentation.md) |

Proposals, profiling reports, and dated acceptance evidence live in the
[maintainer records](../internal/README.md) on GitHub. Published references explain
the maintained behavior; records explain what was proposed or verified at a
particular revision.
