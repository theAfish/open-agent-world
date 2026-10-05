"""Paper colour and layered inks remain bounded, portable drawing data."""
import json

from pydantic import ValidationError
import pytest

from backend.packs.face_design import CardProduction, FaceElement, ProductionLayer, Shape, SurfaceDesign


def surface_data():
    return {
        "width": 360, "height": 240,
        "shapes": [{"id": "paper", "x": 0, "y": 0, "width": 360, "height": 240}],
        "elements": [{"id": "title", "kind": "title", "x": 20, "y": 20, "width": 220, "height": 40}],
        "design": {"production": {"stock": {"type": "cotton", "grain": .85}}},
    }


def test_paper_and_per_layer_inks_roundtrip_without_rewriting_geometry():
    data = surface_data()
    data["design"]["production"]["stock"]["color"] = "#dbe3Ed"
    data["shapes"][0]["print"] = {"opacity": .35, "blend": "multiply"}
    data["elements"][0]["print"] = {"opacity": .8, "blend": "screen"}
    surface = SurfaceDesign.model_validate(data)
    portable = json.loads(surface.model_dump_json())
    assert portable["design"]["production"]["stock"]["color"] == "#dbe3Ed"
    for collection in ("shapes", "elements"):
        assert portable[collection][0]["print"] == data[collection][0]["print"]
        for field in ("x", "y", "width", "height"):
            assert portable[collection][0][field] == data[collection][0][field]
    assert SurfaceDesign.model_validate(portable).model_dump() == portable


def test_legacy_surfaces_do_not_gain_optional_print_or_paper_colour_overrides():
    portable = SurfaceDesign.model_validate(surface_data()).model_dump()
    assert "color" not in portable["design"]["production"]["stock"]
    assert "print" not in portable["shapes"][0]
    assert "print" not in portable["elements"][0]
    assert "placement" not in portable["elements"][0]
    assert SurfaceDesign.model_validate_json(json.dumps(portable)).model_dump() == portable
    assert 'layers' not in portable['design']['production']


def test_explicit_empty_process_stack_overrides_legacy_finishing_and_roundtrips():
    production = CardProduction.model_validate({'finishing': {'foil': .8}, 'layers': []})
    portable = json.loads(production.model_dump_json())
    assert portable['layers'] == []
    assert portable['finishing']['foil'] == .8
    assert CardProduction.model_validate(portable).model_dump() == portable


def test_processes_preserve_author_order_repeated_kinds_and_all_mask_settings():
    production = CardProduction.model_validate({'layers': [
        {'id': 'film', 'kind': 'laminate', 'film': 'laser', 'mask': {'source': 'all'}},
        {'id': 'gold', 'kind': 'foil', 'color': '#dfb958', 'mask': {'source': 'text'}},
        {'id': 'silver', 'kind': 'foil', 'color': '#d8dde2', 'mask': {'source': 'preset', 'preset': 'dots', 'invert': True}},
        {'id': 'emboss', 'kind': 'emboss', 'relief': 'recessed', 'mask': {'source': 'elements', 'elementIds': ['title', 'paper']}},
        {'id': 'uv', 'kind': 'uv', 'enabled': False, 'mask': {'source': 'shapes'}},
    ]})
    portable = json.loads(production.model_dump_json())
    assert [layer['id'] for layer in portable['layers']] == ['film', 'gold', 'silver', 'emboss', 'uv']
    assert portable['layers'][2]['mask']['invert']
    assert portable['layers'][3]['mask']['elementIds'] == ['title', 'paper']
    assert portable['layers'][3]['relief'] == 'recessed'
    assert CardProduction.model_validate(portable).model_dump() == portable


@pytest.mark.parametrize('extra', [
    {'kind': 'script'}, {'strength': -1}, {'strength': float('inf')}, {'roughness': .01},
    {'color': 'url(unsafe)'}, {'film': 'none'}, {'relief': 'javascript'},
    {'mask': {'source': 'png'}}, {'mask': {'png': 'https://example.com/mask.png'}},
    {'mask': {'png': 'data:image/svg+xml;base64,PHN2Zy8+'}}, {'mask': {'png': 'data:image/png;base64,eA=='}},
    {'mask': {'source': 'script'}}, {'mask': {'preset': 'url(unsafe)'}}, {'mask': {'fit': 'distort'}},
    {'mask': {'elementIds': ['title', 'title']}}, {'mask': {'elementIds': ['url(unsafe)']}},
    {'mask': {'elementIds': [f'element-{i}' for i in range(33)]}}, {'mask': {'channel': 'script'}},
])
def test_invalid_process_parameters_and_masks_are_rejected(extra):
    with pytest.raises(ValidationError):
        ProductionLayer.model_validate({'id': 'process-1', 'kind': 'foil', **extra})


def test_process_count_and_identifiers_are_bounded():
    with pytest.raises(ValidationError):
        CardProduction.model_validate({'layers': [{'id': 'same', 'kind': kind} for kind in ['ink', 'foil']]})
    with pytest.raises(ValidationError):
        CardProduction.model_validate({'layers': [{'id': f'pass-{i}', 'kind': 'ink'} for i in range(25)]})


@pytest.mark.parametrize("model,extra", [(Shape, {}), (FaceElement, {"kind": "text"})])
@pytest.mark.parametrize("blend,opacity", [("normal", 0), ("multiply", .5), ("screen", 1)])
def test_supported_inks_and_opacity_boundaries_are_accepted(model, extra, blend, opacity):
    layer = model.model_validate({"id": "layer", "x": 0, "y": 0, "width": 50, "height": 50,
        **extra, "print": {"blend": blend, "opacity": opacity}})
    assert layer.model_dump()["print"] == {"blend": blend, "opacity": opacity}


@pytest.mark.parametrize("colour", ["#fff", "#11223344", "#gg1133", "red", "url(https://example.com/ink)"])
def test_paper_colour_accepts_only_six_digit_hex(colour):
    with pytest.raises(ValidationError):
        CardProduction.model_validate({"stock": {"color": colour}})


@pytest.mark.parametrize("collection", ["shapes", "elements"])
@pytest.mark.parametrize("ink", [
    {"opacity": -.01}, {"opacity": 1.01}, {"opacity": float("nan")}, {"opacity": float("inf")},
    {"blend": "overlay"}, {"blend": "url(unsafe)"}, {"custom_css": "display:none"},
])
def test_shape_and_content_inks_reject_invalid_ranges_blends_and_extra_css(collection, ink):
    data = surface_data()
    data[collection][0]["print"] = ink
    with pytest.raises(ValidationError):
        SurfaceDesign.model_validate(data)


def test_authored_ink_layers_can_print_before_and_after_laminate():
    data = surface_data()
    production = data['design']['production']
    production['print'] = {'layered': True}
    production['layers'] = [
        {'id': 'base-ink', 'kind': 'ink', 'content': {'source': 'all', 'elementIds': []}, 'blend': 'normal'},
        {'id': 'film', 'kind': 'laminate'},
        {'id': 'title-ink', 'kind': 'ink', 'content': {'source': 'elements', 'elementIds': ['title']}, 'blend': 'multiply', 'strength': .6},
    ]
    surface = SurfaceDesign.model_validate(data)
    saved = json.loads(surface.model_dump_json())
    assert saved['design']['production']['print']['layered'] is True
    layers = saved['design']['production']['layers']
    assert [layer['kind'] for layer in layers] == ['ink', 'laminate', 'ink']
    assert layers[2]['content']['elementIds'] == ['title']
    assert SurfaceDesign.model_validate(saved).model_dump() == saved


@pytest.mark.parametrize('extra', [
    {'kind': 'foil', 'pattern': {'motif': 'grid'}},
    {'pattern': {'motif': 'unknown'}}, {'pattern': {'density': 2}},
    {'content': {'source': 'url'}}, {'content': {'elementIds': ['title', 'title']}},
    {'content': {'elementIds': ['invalid id']}}, {'blend': 'overlay'},
])
def test_authored_ink_content_is_bounded(extra):
    with pytest.raises(ValidationError):
        ProductionLayer.model_validate({'id': 'ink-1', 'kind': 'ink', **extra})


@pytest.mark.parametrize('kind', ['ink', 'foil', 'laminate', 'emboss', 'uv'])
def test_every_process_can_own_elements_and_round_trip(kind):
    layer = ProductionLayer.model_validate({'id': 'authored-layer', 'kind': kind,
        'content': {'source': 'elements', 'elementIds': ['title', 'shape-accent']},
        **({'pattern': {'motif': 'grid', 'density': .65}} if kind == 'ink' else {})})
    saved = json.loads(layer.model_dump_json(exclude_none=True))
    assert saved['content']['elementIds'] == ['title', 'shape-accent']
    assert ProductionLayer.model_validate(saved).model_dump() == layer.model_dump()


def test_semantic_elements_are_unique_per_process_not_per_card():
    data = surface_data()
    original = next(item for item in data['elements'] if item['kind'] == 'title')
    data['elements'].append({**original, 'id': 'foil-title'})
    data['design']['production']['print'] = {'layered': True}
    data['design']['production']['layers'] = [
        {'id': 'ink', 'kind': 'ink', 'content': {'source': 'all'}},
        {'id': 'foil', 'kind': 'foil', 'content': {'source': 'elements', 'elementIds': ['foil-title']}}]
    value = SurfaceDesign.model_validate(data)
    assert len([item for item in value.elements if item.kind == 'title']) == 2
    data['design']['production']['layers'][1]['content']['elementIds'].append(original['id'])
    with pytest.raises(ValidationError):
        SurfaceDesign.model_validate(data)
