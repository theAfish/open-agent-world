import pytest

from open_agent_world.plugin_api import AgentRuntimeError
from oaw_codex import discovery


def test_execution_adopts_selected_client_as_external_runtime(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from oaw_codex.runtime import CodexRuntime
    from open_agent_world.plugin_api import ExecutableEnvironmentManager, ExecutableEnvironmentSpec
    from backend.packs.spec import read_source_pack
    runtime = CodexRuntime(SimpleNamespace(managed_environment_root=lambda: tmp_path), state_directory=tmp_path / 'state')
    async def selected(config):
        return {'executable': 'selected-codex.exe'}
    captured = []
    def adopt(manager, spec, *, path, verify):
        captured.append((spec, path, verify))
        return SimpleNamespace(executable=path, management='external')
    monkeypatch.setattr(runtime, '_discover', selected)
    monkeypatch.setattr(ExecutableEnvironmentManager, 'external', adopt)
    assert asyncio.run(runtime._command(None)) == ['selected-codex.exe', 'app-server', '--listen', 'stdio://']
    spec, path, verified = captured[0]
    assert path == 'selected-codex.exe' and verified
    root = Path(__file__).resolve().parents[1]
    declared = read_source_pack(root).environments['client']
    assert spec.fingerprint == ExecutableEnvironmentSpec.from_declaration('openai.codex', 'client', declared, root).fingerprint


def test_auto_prefers_desktop_over_path(tmp_path, monkeypatch):
    desktop = tmp_path / 'desktop' / 'codex.exe'
    desktop.parent.mkdir()
    desktop.touch()
    monkeypatch.setattr(discovery, 'desktop_candidates', lambda: [desktop])
    monkeypatch.setattr(discovery, '_run', lambda args: 'codex-cli 0.153.4')
    monkeypatch.setattr(discovery.shutil, 'which', lambda _: pytest.fail('PATH must not override desktop discovery'))
    found = discovery.discover()
    assert found['source'] == 'desktop'
    assert found['executable'] == str(desktop.resolve())


def test_explicit_desktop_does_not_silently_use_cli(monkeypatch):
    monkeypatch.setattr(discovery, 'desktop_candidates', lambda: [])
    with pytest.raises(AgentRuntimeError, match='Desktop Codex runtime was not found'):
        discovery.discover('desktop')


def test_manual_rejects_shell_wrappers(monkeypatch):
    monkeypatch.setattr(discovery.shutil, 'which', lambda _: 'codex.cmd')
    with pytest.raises(AgentRuntimeError, match='shell wrapper'):
        discovery.discover('manual', 'codex.cmd')
