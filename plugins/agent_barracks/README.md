# Agent Barracks

A first-party Open Agent World plugin for callable Agent and team templates.
Requires Plugin API 1.6. The source checkout loads it by default; an installed
entry-point distribution takes precedence.

1. Create an Agent Barracks card from the Agents deck.
2. Drag an Agent or Legion into the barracks to open a prefilled capture form,
   or open barracks and choose **Save selected as template**. Choose one entry
   Agent and the equipped subgraph to copy. Original nodes stay in place.
3. Give it a name and a useful **When to use** description. Decide which connected
   resources to copy, share, or omit. Copied Sandboxes start with fresh workspaces.
4. Equip **Summoning** on an Agent, then connect that skill to the
   barracks using **Summon agents**. The Agent receives a tool that can list and
   choose templates, summon them for a task, and follow up on retained instances.
   Connecting to one template card grants access only to that template.
5. Use **Try a template** to run from the UI. Review results, continue with the
   same instance, stop it, or reclaim its nodes and workspaces.

To allow recursive summoning, explicitly share the barracks connection when
saving the template. The entire root task shares the configured depth, concurrency
and total-instance limits. Spatial membership does not grant extra permissions.

The plugin owns its node documents, relationship and tool schema. The host owns
subgraph restoration, capability checks, durable instance records and child Runs.
See [the host contract](../../docs/plugins.md#callable-subgraphs-and-agent-barracks-plugin-api-16).
