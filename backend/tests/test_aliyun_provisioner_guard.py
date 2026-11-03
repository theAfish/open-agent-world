"""Sealed root import closure and production release boundary regressions."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy/aliyun' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('provisioner_guard')
remote = load('release_remote')
bootstrap = load('bootstrap')


@pytest.fixture
def config():
    return {'version': 1, 'target': {'app': '/opt/open-agent-world', 'hosting': '/srv/oaw-hosting',
            'releases': '/opt/oaw-releases', 'service': 'oaw-hosted'},
            'source': '/opt/oaw-provisioner/source', 'dependencies': '/opt/oaw-provisioner/dependencies',
            'verify_user': 'oaw-production-verify',
            'source_revision': 'a' * 40,
            'files': {'source': {'backend/__init__.py': 'b' * 64}, 'dependencies': {'dependency.py': 'c' * 64}}}


@pytest.fixture
def policy(config, tmp_path, monkeypatch):
    path = tmp_path / 'provisioner.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(guard, 'POLICY', path)
    monkeypatch.setattr(guard, 'trusted', lambda path, **kw: Path(path))
    monkeypatch.setattr(guard, 'inventory', lambda *args: None)
    monkeypatch.setattr(Path, 'is_file', lambda path: True)
    return path


@pytest.mark.parametrize('key,value', [
    ('source', '/opt/open-agent-world/sealed'),
    ('dependencies', '/opt/oaw-releases/dependencies'),
    ('source', '/opt/oaw-release-operator/source'),
    ('source', '/opt/oaw-provisioner/dependencies/source'),
    ('source_revision', 'reviewed'),
])
def test_registration_rejects_mutable_overlaps_and_unidentified_source(config, policy, key, value):
    config[key] = value
    policy.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='independent|separate|reviewed revision'):
        guard.configuration(config['target'])


def test_registration_binds_target_and_excludes_execution_sandbox(config, policy):
    assert guard.configuration(config['target']) == config
    with pytest.raises(ValueError, match='another installation'):
        guard.configuration({**config['target'], 'service': 'oaw-other'})
    with pytest.raises(ValueError, match='independent'):
        guard.configuration(config['target'], excluded=('/opt/oaw-provisioner',))
    with pytest.raises(ValueError, match='verification group'):
        guard.configuration(config['target'], verify_user='oaw-other-verifier')


@pytest.mark.parametrize('alteration', ['changed', 'extra', 'missing', 'pth', 'editable'])
def test_complete_inventory_rejects_changed_missing_extra_and_startup_files(tmp_path, monkeypatch, alteration):
    monkeypatch.setattr(guard, 'trusted', lambda path, **kw: Path(path))
    item = tmp_path / 'dependency.py'
    item.write_text('reviewed = True')
    expected = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()}
    guard.inventory(tmp_path, expected)
    if alteration == 'changed':
        item.write_text('reviewed = False')
    elif alteration == 'missing':
        item.unlink()
    else:
        filename = {'extra': 'injected.py', 'pth': 'editable.pth', 'editable': '__editable__hook.py'}[alteration]
        extra = tmp_path / filename
        extra.write_text('import malicious')
        if alteration != 'extra':
            expected[filename] = hashlib.sha256(extra.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='changed|inventory|startup|editable'):
        guard.inventory(tmp_path, expected)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX ownership and link boundary')
@pytest.mark.parametrize('alteration', ['writable', 'symlink', 'hardlink'])
def test_complete_tree_trust_rejects_links_and_writable_files(tmp_path, monkeypatch, alteration):
    # The temporary test parent is user-owned; mock only ownership, retaining
    # actual mode/type/link metadata for every filesystem entry.
    original = Path.lstat
    def metadata(path):
        values = list(original(path))
        values[4] = 0
        if path != tmp_path / 'dependency.py' and path != tmp_path / 'link.py' and path != tmp_path / 'hardlink.py':
            values[0] &= ~0o022
        return os.stat_result(values)
    monkeypatch.setattr(Path, 'lstat', metadata)
    item = tmp_path / 'dependency.py'
    item.write_text('reviewed = True')
    item.chmod(0o644)
    if alteration == 'writable':
        item.chmod(0o666)
    elif alteration == 'symlink':
        item = tmp_path / 'link.py'
        item.symlink_to(tmp_path / 'dependency.py')
    else:
        item = tmp_path / 'hardlink.py'
        os.link(tmp_path / 'dependency.py', item)
    with pytest.raises(ValueError, match='links|writable'):
        guard.trusted(item)


def test_units_have_fixed_independent_entrypoint_and_no_application_environment(config):
    units = guard.units(config)
    service = units['oaw-hosted-provisioner.service']
    assert 'ExecStart=/usr/bin/python3 -I -S /opt/oaw-release-operator/provisioner_guard.py tick' in service
    assert 'WorkingDirectory=/' in service
    assert config['target']['app'] not in service
    assert 'EnvironmentFile=' not in service
    assert 'OnUnitInactiveSec=5' in units['oaw-hosted-provisioner.timer']
    assert bootstrap.operator_files('production') == bootstrap.CORE_FILES | {'provisioner_guard.py'}
    assert bootstrap.operator_files('preview') == bootstrap.CORE_FILES


def test_sealed_socket_adapter_changes_only_verifier_access_and_retains_worker_isolation(config):
    from backend.hosting.configuration import GatewaySettings, HostedProfile
    from backend.hosting.systemd import render_units, service_user
    profile = HostedProfile('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
                            'app', 'Existing workspace', 'runtime',
                            'unix:///run/oaw-hosted/11111111-1111-4111-8111-111111111111.sock', 'a' * 64)
    settings = GatewaySettings('https://example.test', Path('/srv/oaw-hosting/control/accounts.sqlite3'), (profile,))
    rendered = render_units(settings, directory=config['target']['hosting'], application=config['target']['app'])
    adapted = guard.verification_sockets(rendered, config)
    for filename in rendered:
        if not filename.endswith('.socket'):
            assert adapted[filename] == rendered[filename]
    socket = adapted['oaw-hosted-worker-' + profile.profile_id + '.socket']
    assert 'SocketUser=' + service_user('oaw-hosted') + '\n' in socket
    assert 'SocketGroup=' + config['verify_user'] + '\n' in socket
    assert 'SocketMode=0660\n' in socket
    assert rendered['oaw-hosted-worker-' + profile.profile_id + '.socket'].endswith('RemoveOnStop=yes\n')
    for changed in ('SocketMode=0666', 'SocketMode=0660', 'SocketMode=0600\nSocketMode=0600'):
        broken = {**rendered, 'oaw-hosted-worker-' + profile.profile_id + '.socket':
                  rendered['oaw-hosted-worker-' + profile.profile_id + '.socket'].replace('SocketMode=0600', changed)}
        with pytest.raises(ValueError, match='socket policy differs'):
            guard.verification_sockets(broken, config)


def test_launcher_ignores_candidate_cwd_pythonpath_and_site_startup(tmp_path):
    source = tmp_path / 'sealed'
    package = source / 'backend/hosting'
    package.mkdir(parents=True)
    (source / 'backend/__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    result_path = tmp_path / 'result.json'
    (package / 'provisioner.py').write_text(
        'import json, os, sys\nfrom pathlib import Path\n'
        'def render_units(*args, **kwargs): return {}\n'
        'def tick(directory, application, name):\n'
        '    Path(' + repr(str(result_path)) + ').write_text(json.dumps({"source": __file__, '
        '"root": os.geteuid(), "factory": os.environ.get("OAW_HOSTED_SERVICES_FACTORY"), '
        '"application": str(application), "name": name}))\n')
    candidate = tmp_path / 'candidate'
    (candidate / 'backend').mkdir(parents=True)
    (candidate / 'backend/__init__.py').write_text('raise RuntimeError("candidate imported as root")')
    (candidate / 'sitecustomize.py').write_text('raise RuntimeError("site startup executed")')
    deps = tmp_path / 'dependencies'
    deps.mkdir()
    (deps / 'malicious.pth').write_text('import nonexistent_malicious_module')
    driver = ('import importlib.util, os\nfrom pathlib import Path\n'
        'spec=importlib.util.spec_from_file_location("guard", ' + repr(str(ROOT / 'deploy/aliyun/provisioner_guard.py')) + ')\n'
        'guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)\n'
        'guard.trusted=lambda path, **kw: Path(path)\n'
        'os.geteuid=lambda: 0\n'
        'guard.execute(' + repr({'source': str(source), 'dependencies': str(deps), 'verify_user': 'oaw-production-verify', 'target': {
            'app': str(candidate), 'hosting': str(tmp_path / 'hosting'), 'service': 'oaw-hosted'}}) + ', "tick")\n')
    env = {**os.environ, 'PYTHONPATH': str(candidate), 'OAW_HOSTED_SERVICES_FACTORY': 'malicious:factory'}
    result = subprocess.run([sys.executable, '-I', '-S', '-c', driver], cwd=candidate, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result_path.read_text())
    assert Path(value['source']).is_relative_to(source)
    assert value['factory'] is None and value['root'] == 0
    assert value['application'] == str(candidate) and value['name'] == 'oaw-hosted'


@pytest.mark.parametrize('failure', [None, 'content', 'DropInPaths', 'FragmentPath', 'NeedDaemonReload',
                                   'socket_mode', 'socket_owner', 'socket_group', 'socket_type'])
def test_production_release_checks_exact_effective_provisioner_units(config, tmp_path, monkeypatch, failure):
    units = guard.units(config)
    identifier = '11111111-1111-4111-8111-111111111111'
    worker_sockets = {'oaw-hosted-worker-' + identifier + '.socket': '[Socket]\n# reviewed frozen renderer output\n'}
    units.update(worker_sockets)
    for name, content in units.items():
        (tmp_path / name).write_text(content + ('# altered' if failure == 'content' else ''))
    monkeypatch.setattr(remote, 'UNITS', tmp_path)
    monkeypatch.setattr(remote, 'profiles', lambda root: [(identifier, Path('/profile'))])
    runtime_socket = Path('/run') / config['target']['service'] / (identifier + '.sock')
    original_exists, original_lstat = Path.exists, Path.lstat
    monkeypatch.setattr(Path, 'exists', lambda path: True if path == runtime_socket else original_exists(path))
    socket_metadata = SimpleNamespace(st_mode=(stat.S_IFLNK if failure == 'socket_type' else stat.S_IFSOCK)
                                     | (0o600 if failure == 'socket_mode' else 0o660),
                                     st_uid=1003 if failure == 'socket_owner' else 1001,
                                     st_gid=1003 if failure == 'socket_group' else 1002)
    monkeypatch.setattr(Path, 'lstat', lambda path: socket_metadata if path == runtime_socket else original_lstat(path))
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda username: SimpleNamespace(
        pw_uid=1002 if username == config['verify_user'] else 1001, pw_gid=1002)))
    monkeypatch.setattr(remote, 'trusted_file', lambda path, **kw: ROOT / 'deploy/aliyun/provisioner_guard.py'
                        if Path(path).name == 'provisioner_guard.py' else Path(path))
    original = importlib.util.module_from_spec
    def module(spec):
        candidate = original(spec)
        spec.loader.exec_module(candidate)
        candidate.configuration = lambda *a, **kw: config
        spec.loader.exec_module = lambda module: None
        return candidate
    monkeypatch.setattr(importlib.util, 'module_from_spec', module)
    def run(*args, **kw):
        if args[0] == '/usr/bin/python3':
            assert args[1:3] == ('-I', '-S') and args[-1] == 'socket-units'
            return json.dumps(worker_sockets)
        prop = next(argument.removeprefix('--property=') for argument in args if argument.startswith('--property='))
        expected = {'FragmentPath': str(tmp_path / args[2]), 'DropInPaths': '', 'NeedDaemonReload': 'no'}[prop]
        return 'unreviewed' if prop == failure else expected
    monkeypatch.setattr(remote, 'run', run)
    if failure is None:
        assert remote.production_provisioner(config['target'], {'sandbox_root': '/opt/execution', 'verify_user': config['verify_user']}) == config
    else:
        with pytest.raises(ValueError, match='provisioner|Production worker socket'):
            remote.production_provisioner(config['target'], {'sandbox_root': '/opt/execution', 'verify_user': config['verify_user']})


def test_production_resume_restarts_only_independently_checked_timer(config, monkeypatch):
    calls = []
    monkeypatch.setattr(remote, 'paths', lambda request: (Path('/app'), Path('/hosting'), Path('/releases')))
    monkeypatch.setattr(remote, 'profiles', lambda hosting: [('existing-profile', Path('/hosting/instances/existing-profile/data'))])
    monkeypatch.setattr(remote, 'production_provisioner', lambda *args: calls.append(('verified',)))
    monkeypatch.setattr(remote, 'run', lambda *args, **kw: calls.append(args))
    remote.resume(config['target'], config={'environment': 'production'})
    assert calls[-2:] == [('verified',), ('systemctl', 'start', 'oaw-hosted-provisioner.timer')]
    assert ('systemctl', 'start', 'oaw-hosted-gateway.service') in calls
    assert not any('oaw-hosted-gateway.socket' in command for command in calls)
    calls.clear()
    remote.resume(config['target'], gateway=False, config={'environment': 'production'})
    assert calls == [('systemctl', 'start', 'oaw-hosted-worker-existing-profile.socket')]


def test_production_stop_quiesces_provisioner_before_enumerating_workers_and_uses_native_gateway(config, monkeypatch):
    calls = []
    monkeypatch.setattr(remote, 'paths', lambda request: (Path('/app'), Path('/hosting'), Path('/releases')))
    def profiles(hosting):
        assert calls == [('systemctl', 'stop', 'oaw-hosted-provisioner.timer', 'oaw-hosted-provisioner.service')]
        return [('existing-profile', Path('/hosting/instances/existing-profile/data'))]
    monkeypatch.setattr(remote, 'profiles', profiles)
    monkeypatch.setattr(remote, 'run', lambda *args, **kw: calls.append(args))
    remote.stop(config['target'], config={'environment': 'production'})
    assert 'oaw-hosted-gateway.service' in calls[-1]
    assert not any('oaw-hosted-gateway.socket' in command for command in calls)


def test_real_provisioner_closure_imports_only_reviewed_source_and_explicit_dependencies(tmp_path):
    """Exercise real deferred imports without running a queue or cloud operation."""
    import sysconfig
    candidate = tmp_path / 'candidate'
    (candidate / 'backend').mkdir(parents=True)
    marker = tmp_path / 'poison-executed'
    poison = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\nraise RuntimeError("candidate imported")\n'
    for path in (candidate / 'backend/__init__.py', candidate / 'sitecustomize.py', candidate / 'pydantic.py'):
        path.write_text(poison)
    dependencies = sorted({sysconfig.get_path('purelib'), sysconfig.get_path('platlib')})
    result_path = tmp_path / 'closure.json'
    driver = '''import importlib, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
source = Path(SOURCE).resolve()
dependencies = [Path(value).resolve() for value in DEPENDENCIES]
# -I -S excludes cwd, PYTHONPATH, site startup and editable-install hooks. Only
# these explicitly selected reviewed package roots are added to system stdlib.
stdlib = [Path(value).resolve() for value in sys.path if value]
sys.path.extend([str(source), *[str(value) for value in dependencies]])
requested = ('backend.hosting.provisioner', 'backend.hosting.templates', 'backend.cloud.aliyun.provisioning')
for name in requested:
    importlib.import_module(name)
origins = {}
for name, module in list(sys.modules.items()):
    filename = vars(module).get('__file__') if module is not None else None
    if filename:
        path = Path(filename).resolve()
        approved = any(path.is_relative_to(root) for root in [source, *dependencies, *stdlib])
        # A base interpreter's global site-packages is not part of system
        # stdlib. Accept distribution code only from explicit dependency roots.
        if 'site-packages' in path.parts or 'dist-packages' in path.parts:
            approved = any(path.is_relative_to(root) for root in dependencies)
        if not approved:
            raise RuntimeError('Unreviewed import origin: ' + name + ': ' + str(path))
        origins[name] = str(path)
Path(RESULT).write_text(json.dumps(origins))
'''.replace('SOURCE', repr(str(ROOT))).replace('DEPENDENCIES', repr(dependencies)).replace('RESULT', repr(str(result_path)))
    env = {**os.environ, 'PYTHONPATH': str(candidate), 'PYTHONHOME': str(candidate),
           'OAW_HOSTED_SERVICES_FACTORY': 'unreviewed:factory'}
    result = subprocess.run([sys.executable, '-I', '-S', '-c', driver], cwd=candidate, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    origins = json.loads(result_path.read_text())
    for name in ('backend.hosting.provisioner', 'backend.hosting.templates', 'backend.cloud.aliyun.provisioning'):
        assert Path(origins[name]).is_relative_to(ROOT)
    assert any(Path(origins['pydantic']).is_relative_to(Path(path)) for path in dependencies)
    assert not marker.exists()
