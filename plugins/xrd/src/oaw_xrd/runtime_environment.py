"""Host adapter for the declared XRD scientific worker environment."""
from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent


def environment_spec():
    from open_agent_world.plugin_api import PythonEnvironmentSpec
    from packaging.requirements import Requirement
    requirements = tuple(str(Requirement(line.strip())) for line in
        (PACKAGE / 'OAW_XRDfit/requirements.txt').read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#'))
    return PythonEnvironmentSpec('research.xrd', 'scientific', requirements, '>=3.10',
        ('numpy', 'scipy', 'pandas', 'pymatgen', 'tensorflow'), PACKAGE / 'verify_environment.py')


def manager(data_root=None):
    from open_agent_world.plugin_api import PackEnvironmentManager
    if data_root is None:
        from .profile import current_data_root
        data_root = current_data_root()
    return PackEnvironmentManager(data_root)


def prepare(data_root=None):
    current = manager(data_root)
    return current.prepare_python(environment_spec(), seed_roots=(
        manager(Path(sys.prefix) / 'oaw-runtime-bundle').environment_root(environment_spec()),))


def qualx_environment_spec():
    from open_agent_world.plugin_api import ExecutableEnvironmentSpec
    return ExecutableEnvironmentSpec('research.xrd', 'qualx', (), PACKAGE,
        ('qualx',), ('--help',), r'(?s)(?=.*--settings-dir)(?=.*--database)(?=.*--wavelength)', True)


def prepare_qualx(data_root=None):
    from open_agent_world.plugin_api import ExecutableEnvironmentManager
    from .profile import roots, current_data_root
    data_root = data_root if data_root is not None else current_data_root()
    root, _ = roots(data_root)
    manager = ExecutableEnvironmentManager(data_root)
    spec = qualx_environment_spec()
    override = os.environ.get('OAW_XRD_QUALX_EXECUTABLE')
    legacy = root / 'engines/qualx3/bin' / ('qualx.exe' if os.name == 'nt' else 'qualx')
    if override or legacy.is_file():
        return manager.external(spec, path=override or legacy, verify=True)
    try:
        return manager.read_external(spec)
    except RuntimeError:
        return manager.external(spec, verify=True)


def worker_command(worker, arguments, *, data_root=None, require_qualx=False):
    """Prepare once before launch; workers never install into either interpreter."""
    from .profile import roots
    root, run_root = roots(data_root)
    override = os.environ.get('OAW_XRD_PYTHON')
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('PYTHON') and key.upper() not in {'VIRTUAL_ENV', 'CONDA_PREFIX'}}
    env.update(OAW_XRD_ROOT=str(root), OAW_XRD_RUN_ROOT=str(run_root))
    if require_qualx:
        env['OAW_XRD_QUALX_EXECUTABLE'] = prepare_qualx(data_root).executable
    if override:
        python = Path(override).expanduser().resolve()
        if not python.is_file():
            raise ValueError(f'Configured XRD Python does not exist: {python}')
        # Explicit legacy overrides remain externally owned. Their site-packages
        # are intentional; -I still excludes host PYTHONPATH/user packages.
        command = [str(python), '-I', '-X', 'utf8', '-u', str(PACKAGE / 'worker_launcher.py'), '-', worker, *map(str, arguments)]
        return command, env, nullcontext()
    current, spec = manager(data_root), environment_spec()
    try:
        handle = current.read_python(spec)
    except RuntimeError:
        bundled = manager(Path(sys.prefix) / 'oaw-runtime-bundle')
        try:
            handle = bundled.read_python(spec)
        except RuntimeError:
            handle = prepare(data_root)
    env['OAW_XRD_ENVIRONMENT'] = str(handle.root)
    command = [handle.python, '-I', '-S', '-X', 'utf8', '-u', str(PACKAGE / 'worker_launcher.py'), str(handle.root), worker, *map(str, arguments)]
    return command, env, current.lease(handle)
