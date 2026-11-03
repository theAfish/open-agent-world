"""Portable bootstrap trust tests; no SSH, cloud or systemd mutations."""
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.hosting.configuration import GatewaySettings, HostedProfile, default_upstream
from backend.hosting.preview import preview_units


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('aliyun_bootstrap', ROOT / 'deploy/aliyun/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def config(tmp_path, monkeypatch):
    source = tmp_path / 'reviewed-operator'
    source.mkdir()
    files = {}
    for name in bootstrap.CORE_FILES:
        path = source / name
        # Even an explicitly reviewed operator is copied as data, never imported.
        path.write_text('raise AssertionError("reviewed code was executed by bootstrap")\n')
        files[name] = bootstrap.digest(path)
    monkeypatch.setattr(bootstrap, 'run', lambda *args: pytest.fail('Unexpected host mutation'))
    return {'version': 1, 'operator': '/opt/oaw-release-operator-preview', 'source_directory': str(source),
        'operator_files': files, 'approved_artifacts': [], 'production_origin': 'https://118.31.119.91',
        'production_target': {'app': '/opt/open-agent-world', 'hosting': '/srv/oaw-hosting',
                              'releases': '/opt/oaw-releases', 'service': 'oaw-hosted'},
        'execution': {'version': 1, 'environment': 'preview',
            'target': {'app': '/opt/oaw-preview-app', 'hosting': '/srv/oaw-preview-hosting',
                       'releases': '/opt/oaw-preview-releases', 'service': 'oaw-preview'},
            'build_user': 'oaw-preview-build', 'verify_user': 'oaw-preview-verify',
            'python': '/usr/bin/python3.12', 'sandbox_root': '/opt/oaw-preview-execution'},
        'preview': {'public_origin': 'https://preview.example.invalid', 'setup_user': 'oaw-preview-setup'}}


def test_default_plan_copies_or_executes_nothing(config, tmp_path):
    path = tmp_path / 'bootstrap.json'
    path.write_text(json.dumps(config))
    before = {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()}
    result = bootstrap.main(['--config', str(path)])
    assert result['apply'] is False
    assert result['enable_services'] is result['start_services'] is result['routing_changes'] is False
    assert result['root_application_imports'] is result['production_mutations'] is False
    assert {item: item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()} == before
    commands = result['preview_preparation']
    assert '--property=User=oaw-preview-setup' in commands['initialize']
    assert '--property=PrivateNetwork=yes' in commands['initialize']
    assert '--property=RootDirectory=/opt/oaw-preview-execution' in commands['initialize']
    assert commands['initialize'][-1] == '/srv/oaw-preview-hosting.passwords.json'


def test_production_example_pins_the_guard_without_installing_or_starting_services(config):
    value = json.loads((ROOT / 'deploy/aliyun/bootstrap.production.example.json').read_text(encoding='utf-8'))
    value['source_directory'] = config['source_directory']
    guard = Path(config['source_directory']) / 'provisioner_guard.py'
    guard.write_text('independently reviewed guard')
    value['operator_files'] = {**config['operator_files'], guard.name: bootstrap.digest(guard)}
    result = bootstrap.plan(value)
    assert result['execution']['environment'] == 'production'
    assert result['execution']['target'] == value['production_target']
    assert set(result['operator_files']) == bootstrap.CORE_FILES | {'provisioner_guard.py'}
    assert result['apply'] is result['start_services'] is result['enable_services'] is False
    assert 'preview_preparation' not in result


@pytest.mark.parametrize('key,value', [
    ('app', '/opt/open-agent-world/preview'),
    ('hosting', '/srv/oaw-hosting'),
    ('releases', '/opt/oaw-preview-app/releases'),
    ('service', 'oaw-hosted'),
])
def test_preview_cannot_register_production_or_overlapping_target(config, key, value):
    config['execution']['target'][key] = value
    with pytest.raises(ValueError, match='overlap|separate service'):
        bootstrap.plan(config)


def test_operator_and_production_registration_are_pinned(config):
    config['operator'] = '/opt/oaw-release-operator'
    with pytest.raises(ValueError, match='registered environment'):
        bootstrap.plan(config)
    config['execution']['environment'] = 'production'
    with pytest.raises(ValueError, match='protected production target'):
        bootstrap.plan(config)


@pytest.mark.parametrize('url', ['https://118.31.119.91:8443', 'https://production.example:8443',
                               'https://preview.example.invalid/preview'])
def test_preview_requires_a_separate_https_hostname(config, url):
    config['production_origin'] = 'https://production.example'
    config['preview']['public_origin'] = url
    with pytest.raises(ValueError, match='hostname|HTTPS origin'):
        bootstrap.plan(config)


@pytest.mark.parametrize('origin', ['http://127.0.0.1:1024', 'http://127.0.0.1:8765', 'http://[::1]:65535'])
def test_explicit_private_preview_plans_exact_loopback_origin_without_changing_isolation(config, origin):
    public_command = bootstrap.plan(config)['preview_preparation']['initialize']
    assert '--allow-local-http' not in public_command
    config['preview'].update(public_origin=origin, allow_local_http=True)
    result = bootstrap.plan(config)
    command = result['preview_preparation']['initialize']
    assert command[command.index('--origin') + 1] == origin
    assert command[-1] == '--allow-local-http'
    assert '--property=PrivateNetwork=yes' in command
    assert '--property=RestrictAddressFamilies=AF_UNIX' in command
    assert result['routing_changes'] is result['start_services'] is result['production_mutations'] is False


@pytest.mark.parametrize('origin', [
    'http://127.0.0.1:8765', 'http://[::1]:8765',
])
def test_private_preview_requires_explicit_opt_in(config, origin):
    config['preview']['public_origin'] = origin
    with pytest.raises(ValueError, match='HTTPS origin'):
        bootstrap.plan(config)


@pytest.mark.parametrize('origin', [
    'http://localhost:8765', 'http://127.0.0.2:8765', 'http://0.0.0.0:8765',
    'http://preview.example:8765', 'http://127.1:8765', 'http://[0:0:0:0:0:0:0:1]:8765',
    'https://127.0.0.1:8765', 'http://127.0.0.1', 'http://127.0.0.1:0',
    'http://127.0.0.1:80', 'http://127.0.0.1:1023', 'http://127.0.0.1:65536',
    'http://127.0.0.1:08765', 'http://127.0.0.1:+8765', 'http://127.0.0.1:bad',
    'http://127.0.0.1:8765/', 'http://127.0.0.1:8765?query', 'http://127.0.0.1:8765#fragment',
    'http://alice@127.0.0.1:8765', 'http://127.0.0.1:8765\\',
    ' http://127.0.0.1:8765', 'http://127.0.0.1:8765\n', 'http://127.0.0.1:\t8765',
])
def test_private_preview_rejects_noncanonical_or_nonloopback_origins(config, origin):
    config['preview'].update(public_origin=origin, allow_local_http=True)
    with pytest.raises(ValueError, match='exact HTTP loopback'):
        bootstrap.plan(config)


@pytest.mark.parametrize('flag', ['true', 'false', 1, 0, None])
def test_private_preview_flag_must_be_a_json_boolean(config, flag):
    config['preview']['allow_local_http'] = flag
    with pytest.raises(ValueError, match='must be a boolean'):
        bootstrap.plan(config)


def test_private_preview_keeps_https_production_and_distinct_cookie_hostname(config):
    config['preview'].update(public_origin='http://127.0.0.1:8765', allow_local_http=True)
    config['production_origin'] = 'http://production.example'
    with pytest.raises(ValueError, match='HTTPS origin'):
        bootstrap.plan(config)
    config['production_origin'] = 'https://127.0.0.1'
    with pytest.raises(ValueError, match='distinct hostname'):
        bootstrap.plan(config)


def test_production_cannot_contain_private_preview_setup(config):
    config['execution'].update(environment='production', target=config['production_target'])
    config['operator'] = bootstrap.OPERATOR_ROOTS['production']
    guard = Path(config['source_directory']) / 'provisioner_guard.py'
    guard.write_text('independently reviewed guard')
    config['operator_files'][guard.name] = bootstrap.digest(guard)
    config['preview'].update(public_origin='http://127.0.0.1:8765', allow_local_http=True)
    with pytest.raises(ValueError, match='cannot contain Preview setup'):
        bootstrap.plan(config)


def test_changed_reviewed_file_fails_before_any_apply_step_and_preserves_production(config, tmp_path, monkeypatch):
    marker = tmp_path / 'production.sqlite3'
    marker.write_bytes(b'production must not change')
    (Path(config['source_directory']) / 'release_remote.py').write_text('changed after review')
    monkeypatch.setattr(bootstrap, 'check_admin', lambda value: pytest.fail('Apply proceeded past failed review'))
    with pytest.raises(ValueError, match='Reviewed file changed'):
        bootstrap.install_operator(config)
    assert marker.read_bytes() == b'production must not change'


def test_no_additional_import_module_can_enter_reviewed_operator(config):
    config['operator_files']['json.py'] = 'a' * 64
    with pytest.raises(ValueError, match='unexpected reviewed file'):
        bootstrap.plan(config)


@pytest.mark.parametrize('isolated,no_site', [(False, True), (True, False)])
def test_root_apply_requires_isolation_and_disabled_site_callbacks(config, monkeypatch, isolated, no_site):
    monkeypatch.setattr(bootstrap, 'sys', SimpleNamespace(platform='linux',
                        flags=SimpleNamespace(isolated=isolated, no_site=no_site)))
    monkeypatch.setattr(bootstrap.os, 'geteuid', lambda: 0, raising=False)
    with pytest.raises(ValueError, match='system Python -I -S'):
        bootstrap.check_admin(config)


@pytest.mark.parametrize('uid,mode,links', [
    (1000, stat.S_IFREG | 0o600, 1),
    (0, stat.S_IFREG | 0o620, 1),
    (0, stat.S_IFLNK | 0o600, 1),
    (0, stat.S_IFREG | 0o600, 2),
])
def test_control_files_require_root_ownership_no_writable_peers_and_no_links(monkeypatch, uid, mode, links):
    path = ROOT / 'deploy/aliyun/bootstrap.py'
    monkeypatch.setattr(Path, 'resolve', lambda self, **kwargs: self)
    monkeypatch.setattr(Path, 'lstat', lambda self: SimpleNamespace(st_uid=uid, st_mode=mode, st_nlink=links))
    with pytest.raises(ValueError, match='root-owned|real directories|hard links'):
        bootstrap.trusted(path)


@pytest.fixture
def units(config, tmp_path):
    name = config['execution']['target']['service']
    profiles = tuple(HostedProfile(profile_id, str(uuid4()), 'preview', username, 'builder',
                      default_upstream(profile_id, name), secret * 32)
        for username, profile_id, secret in (('Alice', str(uuid4()), 'ab'), ('Bob', str(uuid4()), 'cd')))
    values = preview_units(GatewaySettings(config['preview']['public_origin'], tmp_path / 'accounts.sqlite3', profiles),
        directory=config['execution']['target']['hosting'], application=config['execution']['target']['app'],
        service=name, port=8081, verify_user=config['execution']['verify_user'],
        execution_root=config['execution']['sandbox_root'],
        production_roots=bootstrap.protected_paths(config['production_target']))
    directory = tmp_path / 'reviewed-units'
    directory.mkdir()
    for filename, content in values.items():
        (directory / filename).write_text(content, encoding='utf-8')
    config['preview'].update(unit_source_directory=str(directory),
        unit_files={name: bootstrap.digest(directory / name) for name in values})
    return config, directory


def test_existing_nonroot_generator_matches_independent_root_unit_policy(units):
    config, directory = units
    _, expected, profiles = bootstrap.reviewed_units(config)
    assert len(profiles) == 2 and len(expected) == 8
    assert bootstrap.plan(config, step='preview-units')['start_services'] is False


@pytest.mark.parametrize('reviewed,installed', [(True, False), (False, True), (True, 1), (False, 'false')])
def test_unit_install_requires_matching_boolean_private_http_policy(units, monkeypatch, reviewed, installed):
    config, _ = units
    config['preview'].update(public_origin='http://127.0.0.1:8765' if reviewed else 'https://preview.example.invalid',
                             allow_local_http=reviewed)
    profiles = bootstrap.reviewed_units(config)[2]
    hosting = {
        'public_origin': config['preview']['public_origin'], 'allow_local_http': installed,
        'profiles': [{'profile_id': profile} for profile in profiles]}
    original_read = bootstrap.read
    def read(path):
        if path.name == 'execution.json':
            return config['execution']
        if path.name == 'hosting.json':
            return hosting
        return original_read(path)
    # Model only the external host registration/storage. Keep the real pinned
    # target and independent generated-unit validation intact.
    monkeypatch.setattr(bootstrap, 'read', read)
    monkeypatch.setattr(bootstrap, 'plain_tree', lambda path: [])
    monkeypatch.setattr(bootstrap, 'check_admin', lambda config: None)
    monkeypatch.setattr(bootstrap, 'trusted', lambda *args, **kwargs: None)
    monkeypatch.setattr(bootstrap, 'identity_record', lambda *args: None)
    monkeypatch.setattr(bootstrap, 'require_idle', lambda account: None)
    with pytest.raises(ValueError, match='independent Preview configuration'):
        bootstrap.install_preview_units(config)


@pytest.mark.parametrize('before,after', [
    ('NoNewPrivileges=yes', 'NoNewPrivileges=no'),
    ('CapabilityBoundingSet=', 'CapabilityBoundingSet=CAP_SYS_ADMIN'),
    ('[Service]', '[Service]\nExecStartPre=+/bin/sh -c mutate-production'),
    ('Wants=network-online.target', 'Wants=oaw-hosted.target'),
    ('IPAddressDeny=any', 'IPAddressDeny='),
])
def test_artifact_supplied_hash_cannot_authorize_weakened_units(units, before, after):
    config, directory = units
    filename = 'oaw-preview-gateway.service'
    path = directory / filename
    path.write_text(path.read_text().replace(before, after))
    config['preview']['unit_files'][filename] = bootstrap.digest(path)
    with pytest.raises(ValueError, match='security policy|unrelated services'):
        bootstrap.reviewed_units(config)


def test_sysusers_cannot_modify_production_accounts_even_with_matching_digest(units):
    config, directory = units
    path = directory / 'sysusers.conf'
    path.write_text(path.read_text() + 'u root - "replace root" /root /bin/sh\n')
    config['preview']['unit_files']['sysusers.conf'] = bootstrap.digest(path)
    with pytest.raises(ValueError, match='only the dedicated Preview'):
        bootstrap.reviewed_units(config)
