import pytest

from backend.packs.face_design import FaceElement
from backend.packs.factory_models import FunctionDesign


def test_button_bindings_are_optional_bounded_and_do_not_execute_logic():
    legacy = FunctionDesign().model_dump()
    assert 'button_bindings' not in legacy
    binding = {'mode': 'preview', 'element_id': 'button-generate', 'logic_id': 'generate'}
    design = FunctionDesign(button_bindings=[binding])
    assert design.model_dump()['button_bindings'] == [binding]
    assert design.run({}) == '你好，世界！'
    with pytest.raises(ValueError, match='重复挂载'):
        FunctionDesign(button_bindings=[binding, binding])
    with pytest.raises(ValueError):
        FunctionDesign(button_bindings=[{**binding, 'logic_id': 'alert(1)'}])
    with pytest.raises(ValueError):
        FunctionDesign(button_bindings=[{**binding, 'script': 'alert(1)'}])


@pytest.mark.parametrize('button', [{'action': 'eval'}, {'action': 'open', 'script': 'alert(1)'}, {'action': 'open', 'background': 'url(x)'}])
def test_buttons_reject_unrecognized_actions_and_executable_data(button):
    with pytest.raises(ValueError):
        FaceElement(id='button', kind='button', x=0, y=0, width=80, height=32, button=button)


def test_only_button_elements_accept_button_settings():
    with pytest.raises(ValueError, match='只有按钮'):
        FaceElement(id='title', kind='title', x=0, y=0, width=80, height=32, button={'action': 'delete'})
