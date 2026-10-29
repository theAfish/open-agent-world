from pathlib import Path
import subprocess

import pytest

from oaw_codex import __main__ as launcher


def test_demo_prepares_frontend_before_launching_or_mutating_trial_state(tmp_path, monkeypatch):
    (tmp_path / 'backend').mkdir()
    (tmp_path / 'backend/main.py').touch()
    vite = tmp_path / 'frontend/node_modules/vite/bin/vite.js'
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(launcher.shutil, 'which', lambda name: 'node')
    calls = []

    def fail(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(launcher.subprocess, 'run', fail)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Launched after failed preparation'))
    with pytest.raises(subprocess.CalledProcessError):
        launcher.demo(tmp_path, tmp_path, 'default')
    assert calls == [(['node', str(tmp_path / 'frontend/scripts/prepare-dev.mjs')], {'cwd': tmp_path, 'check': True})]
    assert not (tmp_path / '.open-agent-world').exists()
