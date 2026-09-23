"""Knowledge stores: structured data that Agents write, with provenance they can verify.

Four card types, each a SQLite database in its node storage:

* ``knowledge.facts``: material property measurements (material, property, value, unit, conditions);
* ``knowledge.vectors``: passages with embeddings for semantic and hybrid search;
* ``knowledge.ontology``: entities with aliases and typed relations;
* ``knowledge.structures``: crystal structures (CIF) with composition and symmetry keys.

Stores connect only to Agents (Read / Curate). They are never derived from sources:
an Agent writes a record explicitly and cites Paper pages it can read at that moment
(common.verify_citations). The citation is pinned to the Paper's fingerprint; a later
check reports records whose source changed, and changes nothing by itself.
"""
from open_agent_world.plugin_api import PackDefinition, PluginDescriptor

from . import facts, ontology, structures, vectors


class KnowledgePlugin:
    descriptor = PluginDescriptor(id="research.knowledge", version="0.1.0", plugin_api_version="1.26",
        name="Knowledge", description="Agent-written fact tables, vector stores, ontologies and structure databases with verified provenance",
        requires_plugins=("research.library",))

    def register(self, registration):
        for module in (facts, vectors, ontology, structures):
            module.register(registration)
        registration.register_pack(PackDefinition(id="research.knowledge.default", name="Knowledge stores",
            description="Fact table, vector store, ontology and structure database for research Agents.",
            cards=tuple(registration.nodes), accent_color="#5f8fb0"))


def create_plugin():
    return KnowledgePlugin()
