"""Placement invariants independent of the summoning workflow."""
from backend.plugins.containers import NodeContainerDefinition
from backend.spatial import Rectangle, find_free_region
from backend.tests.conftest import create_node
from backend.world.layout import WorldLayout, card_footprints, compact_member_region
from backend.world.models import Point


def test_compact_members_wrap_without_overlap_or_long_strip():
    spec = NodeContainerDefinition()
    occupied = []
    for _ in range(40):
        region = compact_member_region(96, 96, occupied, spec, Point(x=100, y=200))
        assert all(not region.overlaps(other, gap=24) for other in occupied)
        occupied.append(region)
    width = max(rect.x + rect.width for rect in occupied) - 100 + 24
    height = max(rect.y + rect.height for rect in occupied) - 200 + 24
    assert width / height < 1.5
    assert height / width < 1.5
    assert len({rect.y for rect in occupied}) > 1


def test_free_region_preserves_clear_positions_and_respects_gap():
    preferred = Rectangle(0, 0, 100, 80)
    assert find_free_region(preferred, []) == preferred
    obstacles = [Rectangle(-20, -30, 180, 140), Rectangle(250, -100, 90, 300)]
    placed = find_free_region(preferred, iter(obstacles), gap=35)
    assert (placed.width, placed.height) == (100, 80)
    assert all(not placed.overlaps(obstacle, gap=35) for obstacle in obstacles)
    assert placed == find_free_region(preferred, reversed(obstacles), gap=35)


def test_layout_can_ignore_a_moving_card_without_mutating_world(client):
    node = create_node(client, "text", position={"x": 600, "y": 400})
    world = client.app.state.services.world
    layout = WorldLayout.capture(world)
    preferred = layout.footprints[node["id"]]
    assert layout.place_region(preferred, exclude_ids=[node["id"]]) == preferred
    assert layout.place_region(preferred) != preferred
    assert world.get_card(node["id"]).position == Point(x=600, y=400)


def test_virtual_workspace_append_fills_rows_and_preserves_existing_members(client):
    from backend.world.models import CardCreate, CardPatch
    world = client.app.state.services.world
    container = world.create_card(CardCreate(type="core.virtual-workspace"))
    spec = world.registry.node_type(container.type).container
    saved = {}
    for _ in range(12):
        node = world.create_card(CardCreate(type="text", position={"x": 4000, "y": 4000}))
        plan = WorldLayout.capture(world).plan_container_append(container, world.list_members(container.id), [node], spec)
        position = Point(x=node.position.x + plan.offset.x, y=node.position.y + plan.offset.y)
        world.update_card(node.id, CardPatch(position=position, parent_id=container.id))
        container = world.update_card(container.id, CardPatch(size=plan.size))
        saved[node.id] = position
    assert container.size.width / container.size.height < 1.6
    assert container.size.height / container.size.width < 1.6
    assert len({point.x for point in saved.values()}) > 1
    assert len({point.y for point in saved.values()}) > 1
    assert all(world.get_card(key).position == point for key, point in saved.items())


def test_generic_container_plan_uses_its_spec_and_preserves_group_geometry(client):
    first = create_node(client, "text", position={"x": 200, "y": 200})
    second = create_node(client, "text", position={"x": 700, "y": 400})
    world = client.app.state.services.world
    nodes = [world.get_card(item["id"]) for item in (first, second)]
    spec = NodeContainerDefinition(content_inset=(60, 180, 70, 90), min_size=(1000, 650))
    plan = WorldLayout.capture(world).plan_container(nodes, spec, preferred=Point(x=2000, y=1000))
    assert plan.position == Point(x=2000, y=1000)
    assert plan.size.width >= 1000 and plan.size.height >= 650
    assert set(plan.root_node_ids) == {first["id"], second["id"]}
    for rect in card_footprints(nodes, world.registry).values():
        assert rect.x + plan.offset.x >= plan.position.x + 60
        assert rect.y + plan.offset.y >= plan.position.y + 180
        assert rect.x + rect.width + plan.offset.x <= plan.position.x + plan.size.width - 70
        assert rect.y + rect.height + plan.offset.y <= plan.position.y + plan.size.height - 90
    assert world.get_card(first["id"]).position == nodes[0].position
