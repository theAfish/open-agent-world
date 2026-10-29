# Technical reference

Use these pages to look up exact behavior after following a guide. For a first
project, start with the [Card App tutorial](../developers/card-app.md).

| Question | Contract |
| --- | --- |
| Which Python API registers cards? | [Plugin API directory](../plugins.md) |
| What does a connection authorize? | [Relationships and capabilities](plugin-capabilities.md) |
| Where do configuration, content and state live? | [Registration](plugin-registration.md), [documents](plugin-documents.md), [card state](../card-state.md) |
| What is inside an installable Pack? | [Distribution](../pack-distribution.md), [source manifest](pack-manifest.md) |
| How do React views load? | [Frontend contract](plugin-frontend.md) |
| What happens on restart/removal? | [Plugin lifecycle](plugin-lifecycle.md), [durable outputs](../lifecycle-artifacts.md) |
| How are work and model context managed? | [Runs](../runs.md), [execution](../execution.md), [context](../context-management.md) |
| What can a Sandbox access? | [Workspace](../sandbox-workspace.md), [networking](../sandbox-networking.md), [security](../security.md) |
| What changes with deployment? | [Plugin deployment](../plugin-deployment.md) |

These contracts describe dev. Minimum API versions are compatibility facts;
retained migration limits describe data still encountered by the loader. Neither
is a promise that every released host contains the latest functionality.
