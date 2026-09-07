"""Read-only world layout snapshots and reusable container placement plans."""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.plugins.containers import NodeContainerDefinition
from backend.plugins.registry import PluginRegistry
from backend.spatial import Rectangle, find_free_region
from backend.world.models import Card, Point, Size
from backend.world.store import WorldStore


def card_footprints(nodes: Sequence[Card], registry: PluginRegistry) -> dict[str, Rectangle]:
    """Reserve container minima and member-driven growth, including older small records.

    Ordinary cards reserve a conservative preview footprint. Local inspector and
    workspace expansion belongs to the frontend and is not part of this snapshot.
    Equipment is presented by its owner rather than occupying its stored position.
    """
    nodes = [node for node in nodes if not node.equipment]
    children: dict[str | None, list[Card]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)
    bounds: dict[str, Rectangle] = {}

    def footprint(node: Card) -> Rectangle:
        if node.id in bounds:
            return bounds[node.id]
        spec = registry.node_type(node.type).container
        if spec:
            width, height = max(node.size.width, spec.min_size[0]), max(node.size.height, spec.min_size[1])
            left, top, right, bottom = spec.content_inset
            for member in children.get(node.id, []):
                member_bounds = footprint(member)
                width = max(width, max(member_bounds.x - node.position.x, left) + member_bounds.width + right)
                height = max(height, max(member_bounds.y - node.position.y, top) + member_bounds.height + bottom)
            result = Rectangle(node.position.x, node.position.y, width, height)
        else:
            # Card positions anchor the compact node, while previews grow around it.
            width, height = max(300, node.size.width), max(190, node.size.height)
            result = Rectangle(node.position.x - width / 2, node.position.y - height / 2,
                               width * 1.5, height * 1.5)
        bounds[node.id] = result
        return result

    return {node.id: footprint(node) for node in nodes}


@dataclass(frozen=True, slots=True)
class ContainerPlacement:
    position: Point
    size: Size
    offset: Point
    root_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorldLayout:
    footprints: Mapping[str, Rectangle]
    registry: PluginRegistry

    @classmethod
    def capture(cls, world: WorldStore) -> "WorldLayout":
        return cls(card_footprints(world.list_cards(), world.registry), world.registry)

    def place_region(self, preferred: Rectangle, *, exclude_ids: Iterable[str] = (), gap: float = 80) -> Rectangle:
        """Plan a new region, or ignore explicit existing IDs when planning a move."""
        excluded = set(exclude_ids)
        return find_free_region(preferred, (rect for key, rect in self.footprints.items() if key not in excluded), gap=gap)

    def plan_container(self, nodes: Sequence[Card], spec: NodeContainerDefinition, *,
                       preferred: Point | None = None, gap: float = 80) -> ContainerPlacement:
        """Enclose a nonempty group, retaining relative positions and equipment ownership.

        Returns only a plan. The caller owns graph mutations, lifecycle rollback,
        and synchronization with other writers. Existing group IDs are excluded.
        """
        ids = {node.id for node in nodes}
        roots = tuple(node.id for node in nodes if node.parent_id not in ids and not node.equipment)
        bounds = card_footprints(nodes, self.registry)
        left, top, right, bottom = spec.content_inset
        x = min(bounds[key].x for key in roots) - left
        y = min(bounds[key].y for key in roots) - top
        size = Size(
            width=max(spec.min_size[0], max(bounds[key].x + bounds[key].width for key in roots) - x + right),
            height=max(spec.min_size[1], max(bounds[key].y + bounds[key].height for key in roots) - y + bottom))
        region = self.place_region(Rectangle(preferred.x if preferred else x, preferred.y if preferred else y,
                                             size.width, size.height), exclude_ids=ids, gap=gap)
        return ContainerPlacement(Point(x=region.x, y=region.y), size,
                                  Point(x=region.x - x, y=region.y - y), roots)
