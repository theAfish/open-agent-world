"""Live graph authorization for read-only dataset consumers."""


def connected_sources(services, reader_id):
    services.world.get_card(reader_id)
    sources = {}
    for edge in services.world.list_edges_from(reader_id):
        relation = services.plugins.relationship(edge.relationship)
        if not relation.data_read or edge.direction not in {"forward", "bidirectional"}:
            continue
        node = services.world.get_card(edge.target)
        definition = services.plugins.node_type(node.type)
        if definition.data_source is not None:
            sources[node.id] = {"id": node.id, "name": node.name, "type": node.type}
    return list(sources.values())
