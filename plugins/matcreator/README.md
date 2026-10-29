# MatCreator on OAW

Use a materials-research Agent with a Conversation, Sandbox, Structure Viewer,
knowledge tools and tracked jobs. The **MatCreator research** formation provides
connected cards and a saved workspace layout.

## Install and start

Install the MatCreator Pack and its required Packs through the Library. In a
source checkout use [setup](../../docs/developers/setup.md) with the intended
profile and `--plugin-path plugins/matcreator`. The Pack declares its private
Python worker environment in [oaw.pack.toml](oaw.pack.toml); it is separate from
the host interpreter and Sandbox environments.

Open MatCreator in the Library, collect its cards and place **MatCreator research**.
Choose a model for its Agent. Select and start a usable Sandbox before asking for
execution. Open Conversation and send a small, bounded task; inspect tool activity
and produced files before trusting the result.

See the [Chinese workspace walkthrough](../../docs/user-guide/matcreator-example.zh-CN.md)
for the visible layout and controls. Existing formations retain their saved layout;
adding a newly available card does not rewrite an existing formation.

## Follow work

**Agent graph** shows execution history. **Jobs → Agent** selects tracked jobs in
the current session: **Observe jobs** reads them; **Manage jobs** additionally
allows provider-supported controls. Pausing monitoring leaves external work
running; cancellation is confirmed only when the provider reports it.

Grouping cards grants no tool or data access. Agents use their live connections;
scientific results and model-generated knowledge still require review.

## Further reading

- [Runtime, dependencies, long computations and maintenance tests](docs/runtime.md).
- [Engine provenance](oaw_matcreator_engine/UPSTREAM.md).
- [Public execution-source API](../../docs/developers/execution-graph.md).

## Delegating research

See [native MatCreator execution](docs/runtime.md#native-matcreator-agent).
