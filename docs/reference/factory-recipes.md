# Factory recipe contract

For the user workflow, see [Card Factory](../card-factory.md).

## Content format


`plugins/card_factory` supplies the devices and workbench. The host's
`backend/packs/factory.py` reuses capture locks, template projection, dependency
inference and the installer. Content Pack schema 2 supports optional
`content.cards`, a list of declarative card JSON paths. `content.legions` may be
empty, but a Pack must contain at least one card or Legion template.

Recipes contain namespaced IDs, face designs and declarative function settings.
They do not execute custom Python or JavaScript, install hooks or make network
requests. Complex workflows can be shared through configured Legions.
Optional `face.studio` v1 stores display modes, semantic designs, shapes and
layers. Legacy recipes without `studio` retain their original configuration.

Run the factory, content-Pack and installation backend tests, the frontend
build, and `e2e/card-factory.spec.ts` when changing this workflow.

Card recipes may include `tutorials` for [progressive tutorials and offline
documentation](../developers/progressive-tutorials.md). The host registers node types
and renders forms; archives still cannot contain Python wheels, JavaScript,
undeclared files or install hooks. Factory devices can be saved in Legions;
the Packer's external source references are cleared when captured.
