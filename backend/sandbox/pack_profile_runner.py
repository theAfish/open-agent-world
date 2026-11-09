"""Stdlib bootstrap copied into an authorized Sandbox RuntimeBundle.

This file executes exclusively inside the Sandbox command tree. Its cache is
Sandbox-writable and never trusted as host executable code or as a permission.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import time
from uuid import uuid4


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def lock(path, timeout=300):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def check_python(constraints):
    current = tuple(sys.version_info[:3])
    for operator, raw in constraints:
        parts = raw.rstrip(".*").split(".")
        version = tuple(map(int, parts))
        expected = (*version, *(0 for _ in range(max(0, 3 - len(version)))))
        equal = current[:len(version)] == version if raw.endswith(".*") else current == expected
        if operator == "~=":
            upper = (*version[:-2], version[-2] + 1, 0) if len(version) >= 3 else (version[0] + 1, 0, 0)
            accepted = expected <= current < upper
        else:
            accepted = {"==": equal, "!=": not equal, ">=": current >= expected,
                        "<=": current <= expected, ">": current > expected, "<": current < expected}[operator]
        if not accepted:
            raise RuntimeError(f"Sandbox Python {platform.python_version()} does not satisfy {operator}{raw}; select a compatible Sandbox")


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(("PYTHON", "PIP_", "UV_"))
            and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX", "NODE_PATH", "NODE_OPTIONS"}}


def run(arguments, *, env, capture=False):
    options = {"stdout": subprocess.PIPE, "text": True} if capture else {}
    return subprocess.run([str(arg) for arg in arguments], check=True, env=env,
                          stdin=subprocess.DEVNULL, **options)


def npm_command():
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("This Sandbox profile needs Node.js and npm installed in the Sandbox")
    if os.name == "nt":
        cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js"
        if not cli.is_file():
            raise RuntimeError("Cannot locate npm-cli.js in this Sandbox; configure its Node.js installation")
        return [node, str(cli)]
    return [npm]


def private_environment(generation, environment):
    venv = generation / "python"
    binary = venv / ("Scripts" if os.name == "nt" else "bin")
    node_root = generation / "node/node_modules"
    env = {**environment, "VIRTUAL_ENV": str(venv), "PYTHONNOUSERSITE": "1",
           "NODE_PATH": str(node_root),
           "PATH": os.pathsep.join((str(binary), str(node_root / ".bin"), environment.get("PATH", "")))}
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"), env


def validate_command(arguments):
    """Validate interpreter selection without parsing or executing shell text."""
    if not arguments or any(not isinstance(item, str) or '\0' in item for item in arguments) or not arguments[0]:
        raise ValueError('Sandbox profile requires a nonempty literal command argv')
    executable = arguments[0].lower()
    basename = PureWindowsPath(executable).name
    python_names = {'python', 'python3', 'python.exe', 'python3.exe', '/usr/bin/python3'}
    pip_names = {'pip', 'pip3', 'pip.exe', 'pip3.exe'}
    if re.fullmatch(r'(?:python|pypy|pip)(?:\d+(?:\.\d+)*)?(?:\.exe)?|py(?:\.exe)?', basename):
        if executable not in python_names | pip_names:
            raise ValueError('Custom Python interpreter paths or versions cannot bind the declared Sandbox profile; use python3')
    if executable not in python_names:
        return 'pip' if executable in pip_names else 'command'
    index = 1
    while index < len(arguments):
        option = arguments[index]
        if option in {'-c', '-m', '--'} or not option.startswith('-'):
            break
        if option in {'-W', '-X', '--check-hash-based-pycs'}:
            index += 2
            continue
        if not option.startswith(('-W', '-X', '--')) and 'S' in option:
            raise ValueError('Python -S bypasses the declared Sandbox dependency environment')
        index += 1
    return 'python'


def command_in_profile(arguments, python, environment):
    kind = validate_command(arguments)
    if kind == 'python':
        # Keep normal cwd/script imports and -c/-m argv semantics. Ignore
        # injected Python settings/user site; the venv excludes system packages.
        return [python, '-E', '-s', *arguments[1:]]
    if kind == 'pip':
        return [python, '-I', '-m', 'pip', *arguments[1:]]
    executable = shutil.which(arguments[0], path=environment['PATH'])
    if not executable:
        raise RuntimeError(f'{arguments[0]} is unavailable in this Sandbox')
    return [executable, *arguments[1:]]


def verify_imports(python, recipe, environment):
    probe = "import importlib,json,sys;[importlib.import_module(name) for name in json.loads(sys.argv[1])]"
    run([python, "-I", "-c", probe, json.dumps(recipe["verify_imports"])], env=environment)


def verify(generation, recipe, environment):
    python, env = private_environment(generation, environment)
    if not python.is_file():
        raise RuntimeError("Sandbox profile interpreter is missing")
    verify_imports(python, recipe, env)
    if recipe["node"]:
        run([*npm_command(), "ls", "--prefix", generation / "node", "--depth=0", "--json"], env=env, capture=True)
    return python, env


def environment_root(recipe):
    # HOME is the Linux/macOS Sandbox home. Windows exposes its own storage as
    # LOCALAPPDATA; never fall back to the host account via pathlib.Path.home().
    home = os.environ.get("HOME") or os.environ.get("LOCALAPPDATA")
    if not home:
        raise RuntimeError("Sandbox does not expose a persistent private home directory")
    identity = {key: value for key, value in recipe.items() if key not in {"command_id", "script_path", "mode", 'sources', 'ownership'}}
    identity.update(provider_version=3, python_version=platform.python_version(), abi=sys.implementation.cache_tag,
                    platform=sysconfig.get_platform())
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Path(home).resolve() / ".oaw/pack-environments" / fingerprint


def _safe_tree(path, parent):
    if path.resolve() != parent.resolve() / path.name:
        raise ValueError('Sandbox environment escapes its managed directory')
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Sandbox environment root is a link; automatic removal skipped')
    # Python venvs contain interpreter symlinks. shutil.rmtree unlinks those
    # entries without following them (also junctions on supported Python>=3.11).
    # The owned root itself must remain a real directory inside this cache.


def _source_key(source):
    return '/'.join(source[key] for key in ('owner', 'environment', 'digest'))


@contextmanager
def runtime_lease(recipe):
    """Reconcile only this host profile during an already-authorized command.

    Remote environments are never deleted by the host filesystem. Other OAW
    profiles and live Sandbox commands retain their own independent references.
    Older admitted commands cannot roll back a newer ownership snapshot.
    """
    root = environment_root(recipe)
    cache = root.parent
    state = cache.parent / 'pack-environment-state'
    for directory in (cache.parent, cache, state):
        if directory.resolve() != directory.absolute():
            raise ValueError('Sandbox dependency cache cannot use redirected directories')
    state.mkdir(parents=True, exist_ok=True)
    lease = lock(state / 'leases' / root.name / (uuid4().hex + '.lock'))
    with lock(state / 'ownership.lock'):
        lease.__enter__()
        try:
            ownership = recipe.get('ownership')
            if ownership:
                profile = ownership['profile']
                if not re.fullmatch('[a-f0-9]{64}', profile):
                    raise ValueError('Invalid Sandbox dependency owner profile')
                snapshot_path = state / (profile + '.json')
                try:
                    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
                except FileNotFoundError:
                    snapshot = ownership
                if ownership['revision'] >= snapshot['revision']:
                    snapshot = ownership
                    write_json(snapshot_path, snapshot)
                desired = {_source_key(source) for source in snapshot['desired']}
                root.mkdir(parents=True, exist_ok=True)
                _safe_tree(root, cache)
                own_path = root / 'owners.json'
                try:
                    owners = json.loads(own_path.read_text(encoding='utf-8'))
                except FileNotFoundError:
                    owners = {'format': 1, 'profiles': {}}
                owners['profiles'][profile] = sorted(set(owners['profiles'].get(profile, ())) |
                    ({_source_key(source) for source in recipe['sources']} & desired))
                write_json(own_path, owners)
                for candidate in cache.iterdir():
                    if not re.fullmatch('[a-f0-9]{64}', candidate.name):
                        continue
                    try:
                        _safe_tree(candidate, cache)
                        references = json.loads((candidate / 'owners.json').read_text(encoding='utf-8'))
                        if references.get('format') != 1:
                            continue
                        if profile in references['profiles']:
                            references['profiles'][profile] = sorted(set(references['profiles'][profile]) & desired)
                            write_json(candidate / 'owners.json', references)
                        if any(references['profiles'].values()):
                            continue
                        busy = False
                        for lease_path in (state / 'leases' / candidate.name).glob('*.lock'):
                            try:
                                with lock(lease_path, timeout=0):
                                    pass
                            except OSError:
                                busy = True
                                break
                        if not busy:
                            with lock(state / (candidate.name + '.prepare.lock'), timeout=0):
                                _safe_tree(candidate, cache)
                                shutil.rmtree(candidate)
                    except (OSError, ValueError, KeyError, TypeError):
                        # Unowned/legacy/cache-corrupt data is not proof of ownership.
                        continue
        except BaseException:
            lease.__exit__(None, None, None)
            raise
    try:
        yield root
    finally:
        lease.__exit__(None, None, None)


def prepare(recipe):
    check_python(recipe["requires_python"])
    names = {sys.platform, os.name, platform.system().lower(), sysconfig.get_platform()}
    for source in recipe["sources"]:
        if source["platforms"] and not names.intersection(source["platforms"]):
            raise RuntimeError(f"Pack environment {source['owner']}/{source['environment']} does not support this Sandbox platform")
    root = environment_root(recipe)
    fingerprint = root.name
    root.mkdir(parents=True, exist_ok=True)
    _safe_tree(root, root.parent)
    environment = clean_environment()
    with lock(root.parent.parent / 'pack-environment-state' / (fingerprint + '.prepare.lock')):
        try:
            selected = json.loads((root / "selected.json").read_text(encoding="utf-8"))
            generation = root / selected["generation"]
            if generation.parent.resolve() != root.resolve() or not generation.name.startswith("generation-"):
                raise ValueError("Invalid Sandbox environment generation")
            receipt = json.loads((generation / "ready.json").read_text(encoding="utf-8"))
            if receipt["fingerprint"] != fingerprint:
                raise ValueError("Sandbox environment does not match its recipe")
            result = verify(generation, recipe, environment)
            write_json(root / "operation.json", receipt)
            return result
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            pass
        try:
            previous = json.loads((root / "operation.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        # A crash between verified receipt and selected-pointer publication is
        # recoverable locally; never repeat a successful installation for that.
        previous_generation = root / previous.get("generation", "")
        if previous_generation.parent.resolve() == root.resolve():
            try:
                receipt = json.loads((previous_generation / "ready.json").read_text(encoding="utf-8"))
                if receipt["fingerprint"] != fingerprint:
                    raise ValueError("Staged Sandbox environment has a different recipe")
                result = verify(previous_generation, recipe, environment)
                write_json(root / "selected.json", {"generation": previous_generation.name})
                write_json(root / "operation.json", receipt)
                return result
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                pass
        operation_id = previous.get("operation_id") or uuid4().hex
        generation = root / ("generation-" + uuid4().hex)
        operation = {"operation_id": operation_id, "command_id": recipe.get("command_id"),
                     "status": "preparing", "fingerprint": fingerprint, "generation": generation.name,
                     "scope": "sandbox-writable", "sources": recipe["sources"]}
        write_json(root / "operation.json", operation)
        generation.mkdir()
        try:
            # Build at the final generation path. Python console scripts and
            # Windows launchers embed this path and must survive publication.
            run([sys.executable, "-I", "-m", "venv", "--without-pip", generation / "python"], env=environment)
            python, env = private_environment(generation, environment)
            if os.name == 'nt':
                # Shell commands use the same public alias as direct commands.
                shutil.copyfile(python, python.with_name('python3.exe'))
            resolved_python = []
            if recipe["python"]:
                run([python, "-I", "-m", "ensurepip", "--upgrade"], env=env)
                run([python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                     *recipe["python"]], env=env)
                resolved_python = json.loads(run([python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "list", "--format=json"], env=env, capture=True).stdout)
            resolved_node = None
            if recipe["node"]:
                run([*npm_command(), "install", "--prefix", generation / "node", "--no-audit", "--no-fund", "--", *recipe["node"]], env=env)
                resolved_node = json.loads(run([*npm_command(), "ls", "--prefix", generation / "node", "--depth=0", "--json"], env=env, capture=True).stdout)
            verify(generation, recipe, environment)
            ready = {**operation, "status": "ready", "resolved_python": resolved_python, "resolved_node": resolved_node}
            write_json(generation / "ready.json", ready)
            write_json(root / "selected.json", {"generation": generation.name})
            write_json(root / "operation.json", ready)
            return python, env
        except BaseException as exc:
            write_json(root / "operation.json", {**operation, "status": "failed", "error": type(exc).__name__})
            raise


def main():
    package = Path(__file__).resolve().parent
    recipe = json.loads((package / "recipe.json").read_text(encoding="utf-8"))
    recipe["command_id"] = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    if recipe.get("management") == "legacy-shared":
        # The host's normal bootstrap owns this read-only environment. Keep
        # the mounted recipe for live admission checks, but no private cache.
        return execute(recipe, package)
    with runtime_lease(recipe):
        return execute(recipe, package)


def execute(recipe, package):
    if recipe.get("management") == "legacy-shared":
        # This process was launched through SharedPythonRuntime.command.
        # Restart its interpreter without -S so its prepared packages load.
        python, environment = Path(sys.executable), clean_environment()
        # The interpreter can survive a failed dependency installation. Check
        # the declared imports before starting the requested command/Skill.
        verify_imports(python, recipe, environment)
    else:
        python, environment = prepare(recipe)
    if recipe['mode'] == 'command':
        return subprocess.call([str(item) for item in command_in_profile(sys.argv[2:], python, environment)],
                               env=environment, stdin=subprocess.DEVNULL)
    script = (package.parent / recipe["script_path"]).resolve()
    if not script.is_relative_to(package.parent) or not script.is_file():
        raise RuntimeError("Skill entrypoint is outside its mounted bundle")
    if recipe["mode"] == "python":
        # Keep local script imports while ignoring user site and PYTHONPATH.
        launcher = "import pathlib,runpy,sys;sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parent));sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
        command = [python, "-I", "-c", launcher, script, *sys.argv[2:]]
    else:
        mode = recipe['mode']
        if mode not in {'node', 'sh', 'bash'}:
            raise RuntimeError('Unsupported Sandbox profile interpreter')
        executable = shutil.which(mode, path=environment['PATH'])
        if not executable:
            raise RuntimeError(f'{mode} is unavailable in this Sandbox')
        command = [executable, script, *sys.argv[2:]]
    # The child remains in the same OS command tree, deadline and resource limits.
    return subprocess.call([str(item) for item in command], env=environment, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Sandbox dependency preparation failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
