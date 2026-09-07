"""Placement invariants independent of the summoning workflow."""
from backend.plugins.containers import NodeContainerDefinition
from backend.spatial import Rectangle, find_free_region
from backend.tests.conftest import create_node
from backend.world.layout import WorldLayout, card_footprints
from backend.world.models import Point


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
