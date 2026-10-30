import ast
import shlex

import pytest

from scripts.aliyun_science_template import PROBE, READY, REQUIREMENTS, template_body


def test_template_bakes_valid_public_install_and_real_import_probe():
    body = template_body({'region': 'cn-hangzhou', 'team': 'test-team', 'api_key': 'must-not-enter-image'}, 'oaw-science-test')
    sandbox = body['runtimeConfig']['sandboxConfig']
    argv = shlex.split(sandbox['startCommand'])
    assert argv[:4] == ['sudo', '-n', 'sh', '-c']
    ast.parse(PROBE)
    assert 'must-not-enter-image' not in str(body)
    assert sandbox['readyCommand'] == 'test -s ' + READY
    assert body['buildConfig']['installStorageClients'] is True
    assert all('==' in item for item in REQUIREMENTS)
    assert 'from mp_api.client import MPRester' in PROBE
    assert 'pip check' in argv[4]
    assert argv[4].endswith('exec sleep infinity')


@pytest.mark.parametrize('name', ['with space', 'x'*37, '../template'])
def test_invalid_template_name_is_rejected(name):
    with pytest.raises(ValueError):
        template_body({'region': 'cn-hangzhou', 'team': 'test-team'}, name)
