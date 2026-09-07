import pytest

from open_agent_world.plugin_api import AgentRuntimeError
from oaw_codex import discovery


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
