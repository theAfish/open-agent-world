"""The optional external engine must not silently fall back to metadata search."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from oaw_xrd import MatchConfig
from oaw_xrd import qualx


STDOUT = 'Searching database...\nFound 2 card(s):\n  [12] Phase A | Si | P 1\n  [34] Phase B | Si O2 | P 1\n'
STDERR = ('d values:  QList(3.1, 2.5)\ndelta values:  QList(0.01, 0.01)\n'
          'Number of strongest matches: 4\nNumber of candidates after restraints: 2\n'
          '"QualxDbManager::makeQueryStrongest->makeQueryInfoIdsWithFom" elapsed time: "0.500" sec\n')


def library(tmp_path):
    path = tmp_path / 'library with spaces.sq'
    for suffix in ('', '.info', '.infostat', '.search'):
        with closing(sqlite3.connect(str(path) + suffix)) as db:
            if suffix == '':
                db.execute('CREATE TABLE id (id INTEGER, name TEXT, mineralname TEXT, chemical_formula TEXT, '
                           'spacegroup TEXT, quality TEXT, rir REAL, nrec INTEGER, dvalue TEXT, intensita TEXT, n INTEGER)')
                db.execute('CREATE TABLE chemical (id INTEGER, chemical_element TEXT)')
            elif suffix == '.search':
                db.execute('CREATE TABLE top (id INTEGER, n INTEGER, dval TEXT)')
            else:
                db.execute('CREATE TABLE marker (id INTEGER)')
            db.commit()
    stat = path.stat()
    return {'node_id': 'connected:slot2', 'revision': 5, 'value': {'kind': 'library', 'path': str(path),
            'filename': path.name, 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'sha256': 'source-library', 'count': 4}}


def test_match_engine_default_and_validation():
    assert MatchConfig().library_engine == 'qualx'
    assert MatchConfig(library_engine='native').library_engine == 'native'
    with pytest.raises(ValueError):
        MatchConfig(library_engine='shell command')


def test_cli_output_requires_real_peak_search_and_complete_candidates():
    ids, stats = qualx.parse_search_output(STDOUT, STDERR)
    assert ids == ['12', '34'] and stats['observed_peak_count'] == 2
    assert stats['strong_peak_candidates'] == 4 and stats['candidate_count'] == 2
    assert stats['internal_search_seconds'] == .5
    with pytest.raises(qualx.QualXError, match='有效'):
        qualx.parse_search_output(STDOUT, '')  # exit=0 metadata fallback is not success
    with pytest.raises(qualx.QualXError, match='不完整'):
        qualx.parse_search_output(STDOUT.replace('[34]', '[12]'), STDERR)
    with pytest.raises(qualx.QualXError, match='不完整'):
        qualx.parse_search_output(STDOUT.replace('2 card', '3 card'), STDERR)
    ids, _ = qualx.parse_search_output('Found 0 card(s):\n', STDERR)
    assert ids == []  # a real search with zero candidates is a valid result


def test_missing_sidecar_is_actionable_without_launching(tmp_path):
    item = library(tmp_path)
    Path(item['value']['path'] + '.search').unlink()
    with pytest.raises(qualx.QualXError, match='配套文件'):
        qualx.validate_files(item['value'])


@pytest.mark.parametrize(('suffix', 'query', 'missing'), [
    ('', 'ALTER TABLE id DROP COLUMN name', 'name'),
    ('', 'DROP TABLE chemical', 'chemical'),
    ('.search', 'ALTER TABLE top DROP COLUMN dval', 'dval'),
])
def test_incompatible_qualx_schema_is_rejected_before_engine_launch(tmp_path, monkeypatch, suffix, query, missing):
    item = library(tmp_path)
    with closing(sqlite3.connect(item['value']['path'] + suffix)) as db:
        db.execute(query)
        db.commit()
    stat = Path(item['value']['path']).stat()
    item['value'].update(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    monkeypatch.setattr(qualx, 'executable_path', lambda: pytest.fail('Incompatible library reached engine startup'))
    with pytest.raises(qualx.QualXError, match=f'结构不兼容.*{missing}'):
        qualx.search_library({'points': [[10 + i, 1] for i in range(30)]}, item, {'wavelength': 1.54})


def test_corrupt_companion_is_rejected_as_database_error(tmp_path):
    item = library(tmp_path)
    Path(item['value']['path'] + '.info').write_bytes(b'not a SQLite database')
    with pytest.raises(qualx.QualXError, match=r'无法读取.*\.info'):
        qualx.validate_files(item['value'])


def test_schema_validation_leaves_all_database_files_unchanged(tmp_path):
    item = library(tmp_path)
    files = [Path(item['value']['path'] + suffix) for suffix in ('', '.info', '.infostat', '.search')]
    before = [(file.read_bytes(), file.stat().st_mtime_ns) for file in files]
    qualx.validate_files(item['value'])
    assert [(file.read_bytes(), file.stat().st_mtime_ns) for file in files] == before


def test_native_library_validation_still_accepts_minimal_peak_schema(tmp_path):
    from oaw_xrd.library import inspect_library, validate_library
    path = tmp_path / 'native.sq'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE id (id INTEGER, chemical_formula TEXT, dvalue TEXT, intensita TEXT)')
        db.execute('INSERT INTO id VALUES (1, "Si", "3,", "100,")')
        db.execute('CREATE TABLE infodb (date TEXT)')
        db.commit()
    doc = inspect_library(path)
    validate_library(doc)
    assert doc['count'] == 1


def test_stock_binary_is_rejected_instead_of_using_global_settings(tmp_path, monkeypatch):
    exe = tmp_path / 'qualx.exe'
    exe.write_bytes(b'test executable')
    monkeypatch.setattr(qualx.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'--nogui --search', stderr=b''))
    with pytest.raises(qualx.QualXError, match='隔离检索接口'):
        qualx.verify_executable(exe)


def test_bridge_binds_only_connected_library_and_archives_exact_input(tmp_path, monkeypatch):
    item = library(tmp_path)
    exe = tmp_path / 'qualx.exe'
    exe.write_bytes(b'bridge executable')
    monkeypatch.setattr(qualx, 'executable_path', lambda: exe)
    monkeypatch.setattr(qualx, 'verify_executable', lambda e: 'binary-hash')
    # Force ASCII staging even if pytest's host path is Unicode.
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs.get('shell', False) is False
        assert command[command.index('--database') + 1] == item['value']['path']
        settings = Path(command[command.index('--settings-dir') + 1])
        assert settings.is_dir() and not list(settings.iterdir())
        assert command[command.index('--composition') + 1] == 'Li AND O AND P AND Ti'
        assert '--contains-any' in command
        kwargs['stdout'].write(STDOUT.encode())
        kwargs['stderr'].write(STDERR.encode())
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualx.subprocess, 'run', run)
    points = [[10 + i * .02, i + .125] for i in range(30)]
    ids, info = qualx.search_library({'points': points}, item, {'wavelength': 1.540593, 'library_elements': 'Li Ti P O'}, run_dir=tmp_path / 'run')
    assert ids == ['12', '34'] and info['library_node_id'] == 'connected:slot2'
    artifact = tmp_path / 'run/qualx/0'
    rows = (artifact / 'pattern.xy').read_text().splitlines()
    assert float(rows[0]) == 1.540593
    assert [[float(x) for x in r.split()] for r in rows[1:]] == points
    saved = json.loads((artifact / 'engine.json').read_text(encoding='utf-8'))
    assert saved['status'] == 'completed' and saved['settings']['isolated'] is True
    assert saved['candidate_count'] == 2 and saved['elapsed_seconds'] >= 0
    assert len(calls) == 1


def test_failed_search_preserves_logs_and_does_not_return_candidates(tmp_path, monkeypatch):
    item = library(tmp_path)
    monkeypatch.setattr(qualx, 'executable_path', lambda: tmp_path / 'engine')
    monkeypatch.setattr(qualx, 'verify_executable', lambda e: 'hash')
    def run(command, **kwargs):
        kwargs['stdout'].write(STDOUT.encode())
        kwargs['stderr'].write(b'failed to load spectrum')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualx.subprocess, 'run', run)
    with pytest.raises(qualx.QualXError, match='有效'):
        qualx.search_library({'points': [[10 + i, 1] for i in range(30)]}, item,
                            {'wavelength': 1.54}, run_dir=tmp_path / 'run')
    record = json.loads((tmp_path / 'run/qualx/0/engine.json').read_text(encoding='utf-8'))
    assert record['status'] == 'failed'
    assert (tmp_path / 'run/qualx/0/stderr.log').read_text() == 'failed to load spectrum'
