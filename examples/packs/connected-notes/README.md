# Connected Notes

Two persistent card types and one passive association, with no Python entrypoint,
frontend build or third-party dependency. The host uses its standard configuration
editor. Traits and associations require Plugin API 1.39, inferred from the manifest.

Copy this directory to your own project. Before publishing, replace
`example.connected-notes` consistently in `oaw.pack.toml` and change the project
name in `pyproject.toml`. Keep your chosen IDs stable across later releases.

From the OAW repository, using its prepared backend interpreter:

```powershell
.\backend\.venv\Scripts\python.exe -m open_agent_world.pack doctor examples/packs/connected-notes
.\backend\.venv\Scripts\python.exe -m open_agent_world.pack check examples/packs/connected-notes --lifecycle --card example.connected-notes.note --card example.connected-notes.topic
.\backend\.venv\Scripts\python.exe -m open_agent_world.pack build-project examples/packs/connected-notes .tmp/connected-notes-0.1.0.oawpack
.\backend\.venv\Scripts\python.exe -m open_agent_world.pack inspect .tmp/connected-notes-0.1.0.oawpack
```

Use a new output path for each build. For an external copy, pass its path in place
of `examples/packs/connected-notes`; the interpreter still belongs to OAW.

1. In a test profile, choose **Library → Packs → Install Pack from File** and
   select the built artifact. A new passive Pack activates without restart.
2. Open **Connected Notes**, collect **Note** and **Topic**, and place one of each
   on the canvas. Edit their fields using the host configuration editor.
3. Connect Note to Topic and select **About topic**. The source must be a Note;
   the target must declare `example.connected-notes.topic`. A Note-to-Note
   connection using this relationship is rejected.
4. Restart OAW and confirm that the cards, saved fields and connection remain.

The association is a user-authored relationship. It grants no tools, data-reading
access or other capabilities, and it does not copy text, route output or execute
the target. Passive cards publish traits in their own owner's namespace;
consumers from other Packs can select the Topic trait as a target.

For an exact-type target instead, replace `target_traits` with
`target_types = ["example.connected-notes.topic"]`. Use both selectors only when
the target must meet both conditions. Required external Packs belong in the
ordinary dependency declaration; a trait name alone does not install anything.

See [the card authoring guide](../../../docs/developers/card-contract.md) and
[the manifest reference](../../../docs/reference/pack-manifest.md) for the contracts.
