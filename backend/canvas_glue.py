"""Shared persistence for the existing browser glue bonds and surface rectangles."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import GraphValidationError, RevisionConflictError
from backend.world.models import Point, Size, CardBatchPatch, CardPatch


class GlueBox(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float
    y: float
    width: float = Field(gt=0, le=4096)
    height: float = Field(gt=0, le=4096)
    level: Literal['node', 'preview', 'inspector', 'workspace'] = 'node'
    sizes: dict[str, Size] | None = None


class GlueBond(BaseModel):
    model_config = ConfigDict(extra='forbid')
    a: str
    b: str
    side: Literal['left', 'right', 'top', 'bottom']


class GluePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    boxes: dict[str, GlueBox] = Field(default_factory=dict, max_length=200)
    bonds: list[GlueBond] = Field(default_factory=list, max_length=200)
    detach: list[str] = Field(default_factory=list, max_length=200)


def _record(services):
    scope = services.state.ensure_scope('canvas', 'world', schema_id='core.canvas')
    return services.state.get_record(scope, 'glue')


def read_glue(services):
    record = _record(services)
    value = deepcopy(record.value)
    cards = {c.id: c for c in services.world.list_cards()}
    bonds = [b for b in value.get('bonds', []) if all(key in cards and not cards[key].parent_id and not cards[key].equipment for key in (b['a'], b['b']))]
    ids = {key for b in bonds for key in (b['a'], b['b'])}
    boxes = {}
    for key, box in value.get('boxes', {}).items():
        if key not in ids:
            continue
        card = cards[key]
        anchor = value.get('anchors', {}).get(key, dict(position=card.position.model_dump(), size=card.size.model_dump()))
        box['x'] += card.position.x - anchor['position']['x']
        box['y'] += card.position.y - anchor['position']['y']
        box['width'] = max(1, box['width'] + card.size.width - anchor['size']['width'])
        box['height'] = max(1, box['height'] + card.size.height - anchor['size']['height'])
        boxes[key] = box
    return dict(revision=record.revision, boxes=boxes, bonds=bonds)


def save_glue(services, value, expected_revision):
    record = _record(services)
    if record.revision != expected_revision:
        raise RevisionConflictError('Glue changed in another canvas; refresh before organizing it')
    value = deepcopy(value)
    value.pop('revision', None)
    value['anchors'] = {key: dict(position=services.world.get_card(key).position.model_dump(),
                                 size=services.world.get_card(key).size.model_dump()) for key in value['boxes']}
    services.state.set(record.scope, 'glue', value, expected_revision=record.revision)
    return read_glue(services)


def component(ids, bonds):
    result = set(ids)
    while True:
        previous = set(result)
        for bond in bonds:
            if result & {bond['a'], bond['b']}:
                result.update((bond['a'], bond['b']))
        if result == previous:
            return result


def patch_glue(services, request):
    value = read_glue(services)
    if value['revision'] != request.revision:
        raise RevisionConflictError('Glue changed in another canvas; refresh before organizing it')
    for key in request.boxes:
        card = services.world.get_card(key)
        if card.parent_id or card.equipment or card.type == 'core.minister' or services.world.is_container(card):
            raise GraphValidationError('Glue applies to ordinary root card surfaces; use container membership or equipment for other attachments')
    value['boxes'].update({key: box.model_dump(exclude_none=True) for key, box in request.boxes.items()})
    value['bonds'] = [b for b in value['bonds'] if not set(request.detach) & {b['a'], b['b']}]
    for item in request.bonds:
        if item.a == item.b or any(key not in value['boxes'] for key in (item.a, item.b)):
            raise GraphValidationError('Glue needs two distinct card surfaces')
        if not any({b['a'], b['b']} == {item.a, item.b} for b in value['bonds']):
            value['bonds'].append(item.model_dump())
    ids = {key for bond in value['bonds'] for key in (bond['a'], bond['b'])}
    value['boxes'] = {key: box for key, box in value['boxes'].items() if key in ids}
    return save_glue(services, value, request.revision)


def expand_glued_updates(services, updates):
    """Reflow bonded peers after saved movement/resize, using existing bond sides."""
    value = read_glue(services)
    requested = {item.node_id for item in updates}
    result = list(updates)
    boxes = deepcopy(value['boxes'])
    for item in updates:
        if item.node_id not in boxes:
            continue
        current = services.world.get_card(item.node_id)
        box = boxes[item.node_id]
        if item.patch.position:
            box['x'] += item.patch.position.x - current.position.x
            box['y'] += item.patch.position.y - current.position.y
        if item.patch.size:
            box['width'] += item.patch.size.width - current.size.width
            box['height'] += item.patch.size.height - current.size.height
    visited = set()
    for item in updates:
        if item.node_id not in boxes or not (item.patch.position or item.patch.size) or item.node_id in visited:
            continue
        queue = [item.node_id]
        visited.add(item.node_id)
        for key in queue:
            for bond in value['bonds']:
                if key not in (bond['a'], bond['b']):
                    continue
                peer_id = bond['b'] if key == bond['a'] else bond['a']
                if peer_id in visited:
                    continue
                visited.add(peer_id)
                queue.append(peer_id)
                if peer_id in requested:
                    continue
                side = bond['side'] if key == bond['a'] else dict(left='right', right='left', top='bottom', bottom='top')[bond['side']]
                a, b, old_a = boxes[key], boxes[peer_id], value['boxes'][key]
                if side in {'left', 'right'}:
                    b['x'] = a['x'] + a['width'] if side == 'right' else a['x'] - b['width']
                    b['y'] += a['y'] - old_a['y']
                else:
                    b['y'] = a['y'] + a['height'] if side == 'bottom' else a['y'] - b['height']
                    b['x'] += a['x'] - old_a['x']
                old_b = value['boxes'][peer_id]
                peer = services.world.get_card(peer_id)
                result.append(CardBatchPatch(node_id=peer_id, patch=CardPatch(position=Point(
                    x=peer.position.x + b['x'] - old_b['x'], y=peer.position.y + b['y'] - old_b['y']))))
                requested.add(peer_id)
    return result


async def change_glue(facade, node_ids, target_id, side, versions, *, detach=False):
    services = facade._services
    async with services._node_mutation():
        scope = facade._scope('glue', *([] if detach else ['move']))
        value = read_glue(services)
        if versions.glue is None or versions.glue != value['revision']:
            raise RevisionConflictError('Inspect the current glue layout before organizing it')
        if not detach and len(node_ids) != 1:
            raise GraphValidationError('Choose one source card to glue to its target')
        ids = component(node_ids + ([] if detach else [target_id]), value['bonds'])
        cards = {key: services.world.get_card(key) for key in ids}
        facade._check(scope, versions, cards.values())
        for card in cards.values():
            if card.parent_id or card.equipment or card.type == 'core.minister' or services.world.is_container(card):
                raise GraphValidationError('Use container membership or equipment for this card; glue requires root card surfaces')
        if detach:
            facade._review('unglue', before=list(cards.values()), organization={'detach': node_ids})
            return patch_glue(services, GluePatch(revision=value['revision'], detach=node_ids))
        source, target = node_ids[0], target_id
        moving = component([source], value['bonds'])
        if target in moving:
            raise GraphValidationError('These cards already belong to the same glue group')
        boxes = {key: value['boxes'].get(key, dict(x=card.position.x, y=card.position.y,
            width=card.size.width, height=card.size.height, level='node')) for key, card in cards.items()}
        a, b = boxes[source], boxes[target]
        # side describes target relative to source, matching the existing browser bond.
        x = b['x'] - a['width'] if side == 'right' else b['x'] + b['width'] if side == 'left' else b['x']
        y = b['y'] - a['height'] if side == 'bottom' else b['y'] + b['height'] if side == 'top' else b['y']
        dx, dy = x - a['x'], y - a['y']
        patches = []
        for key in moving:
            box = boxes[key] = {**boxes[key], 'x': boxes[key]['x'] + dx, 'y': boxes[key]['y'] + dy}
            facade._card(scope, cards[key].model_copy(update={'position': Point(x=box['x'], y=box['y']),
                'size': Size(width=box['width'], height=box['height'])}))
            patches.append((key, {'position': Point(x=cards[key].position.x + dx, y=cards[key].position.y + dy)}))
        await facade._update_batch(patches, versions)
        return patch_glue(services, GluePatch(revision=value['revision'], boxes=boxes,
            bonds=[GlueBond(a=source, b=target, side=side)]))
