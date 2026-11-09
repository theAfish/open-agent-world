"""User-managed reference library: card text added from the UI, stored in a QualX-compatible SQLite file.

The file lives under the XRD data root, is never bundled with the Pack, and is read by the
same native search as mounted `.sq` libraries (`source_cards` records carry each entry's kind).
"""
import base64
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from open_agent_world.plugin_api import ResourceValidationError

from .documents import parse_reference
from .profile import roots

MAX_FILES = 100
MAX_BYTES = 8 * 1024 * 1024
SOURCE = 'OAW managed library'
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS id (id TEXT PRIMARY KEY, chemical_formula TEXT, dvalue TEXT, intensita TEXT)',
    'CREATE TABLE IF NOT EXISTS source_cards (id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS infodb (source TEXT, date TEXT, type TEXT)',
    'CREATE TABLE IF NOT EXISTS structures (id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, cif TEXT)',
)


def _open(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    for statement in SCHEMA:
        db.execute(statement)
    if not db.execute('SELECT 1 FROM infodb').fetchone():
        db.execute('INSERT INTO infodb VALUES (?,?,?)', (SOURCE, time.strftime('%Y-%m-%d'), 'managed'))
    return db


def _library_path(managed):
    if managed and managed.get('path'):
        path = Path(managed['path']).resolve()
        if not path.is_relative_to((roots()[0] / 'libraries').resolve()):
            raise ResourceValidationError('谱库文件不在 XRD 数据目录内')
        return path
    return roots()[0] / 'libraries' / f'{uuid4().hex}.sq'


def _document(path, previous_metadata=None):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stat = path.stat()
    with closing(sqlite3.connect(path)) as db:
        count = db.execute('SELECT count(*) FROM id').fetchone()[0]
    return {'kind': 'library', 'filename': path.name, 'path': str(path), 'sha256': digest,
            'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'count': count,
            'metadata': {**(previous_metadata or {}), 'source': SOURCE, 'version': time.strftime('%Y-%m-%d'),
                         'type': 'managed', 'managed': True, 'reference_kind': 'mixed'},
            'points': [], 'peaks': []}


def _entry(filename, text):
    peaks, metadata = parse_reference(text)
    d = [peak['d'] for peak in peaks]
    intensity = [peak['intensity'] for peak in peaks]
    if max(intensity) <= 0:
        raise ValueError('标准卡片强度必须大于零')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    card_id = metadata['reference_code'] or 'card-' + digest[:12]
    first_line = text.strip().splitlines()[0].lower() if text.strip() else ''
    return {'id': card_id, 'formula': metadata.get('formula', ''),
            'dvalue': ','.join(map(str, d)), 'intensita': ','.join(map(str, intensity)),
            'metadata': {'reference_kind': 'calculated' if 'calculated' in first_line else 'card_export',
                         'name': metadata.get('name', ''), 'wavelength': metadata.get('wavelength'),
                         'source_filename': filename[:200], 'sha256': digest, 'peak_count': len(peaks),
                         'added_at': time.strftime('%Y-%m-%dT%H:%M:%S')}}


def add_cards(value, arguments):
    cards = arguments.get('cards')
    if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_FILES:
        raise ResourceValidationError(f'一次请添加 1–{MAX_FILES} 个卡片文本')
    if sum(len(str(card.get('text', ''))) for card in cards if isinstance(card, dict)) > MAX_BYTES:
        raise ResourceValidationError('卡片文本总量最大 8 MiB')
    path = _library_path(value.get('managed'))
    added, replaced, rejected = 0, 0, []
    with closing(_open(path)) as db:
        for card in cards:
            name = str(card.get('filename', ''))[:200] if isinstance(card, dict) else ''
            try:
                if not isinstance(card, dict) or not isinstance(card.get('text'), str):
                    raise ValueError('卡片需要 filename 与 text')
                entry = _entry(name, card['text'])
            except ValueError as exc:
                rejected.append({'filename': name, 'error': str(exc)})
                continue
            exists = db.execute('SELECT 1 FROM id WHERE id=?', (entry['id'],)).fetchone()
            db.execute('INSERT OR REPLACE INTO id VALUES (?,?,?,?)', (entry['id'], entry['formula'], entry['dvalue'], entry['intensita']))
            db.execute('INSERT OR REPLACE INTO source_cards VALUES (?,?)', (entry['id'], json.dumps(entry['metadata'], ensure_ascii=False)))
            added, replaced = added + (not exists), replaced + bool(exists)
        db.commit()
    if not (added or replaced):
        raise ResourceValidationError('没有可添加的卡片：' + '；'.join(f"{r['filename']}：{r['error']}" for r in rejected[:3]))
    managed = _document(path)
    managed['metadata']['last_import'] = {'added': added, 'replaced': replaced, 'rejected': rejected[:20]}
    return {**value, 'managed': managed}


def remove_entries(value, arguments):
    ids = arguments.get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or not all(isinstance(item, str) for item in ids):
        raise ResourceValidationError('请选择 1–1000 个条目')
    managed = value.get('managed')
    if not managed:
        raise ResourceValidationError('本卡片还没有自有谱库')
    path = _library_path(managed)
    with closing(_open(path)) as db:
        for card_id in ids:
            db.execute('DELETE FROM id WHERE id=?', (card_id,))
            db.execute('DELETE FROM source_cards WHERE id=?', (card_id,))
            db.execute('DELETE FROM structures WHERE id=?', (card_id,))
        db.commit()
    return {**value, 'managed': _document(path)}


def add_structures(value, arguments):
    """Attach CIFs to existing entries: by explicit `id`, or by a filename starting with the entry ID."""
    from .documents import import_input
    from .structures import MAX_CIF_BYTES
    cifs = arguments.get('cifs')
    if not isinstance(cifs, list) or not 1 <= len(cifs) <= MAX_FILES:
        raise ResourceValidationError(f'一次请添加 1–{MAX_FILES} 个 CIF')
    managed = value.get('managed')
    if not managed:
        raise ResourceValidationError('请先添加标准卡片，再附加结构')
    path = _library_path(managed)
    attached, rejected = 0, []
    with closing(_open(path)) as db:
        ids = [row[0] for row in db.execute('SELECT id FROM id')]
        for cif in cifs:
            name = str(cif.get('filename', ''))[:200] if isinstance(cif, dict) else ''
            try:
                if not isinstance(cif, dict) or not isinstance(cif.get('text'), str):
                    raise ValueError('CIF 需要 filename 与 text')
                if len(cif['text'].encode('utf-8')) > MAX_CIF_BYTES:
                    raise ValueError('CIF 最大 2 MiB')
                stem = name.rsplit('.', 1)[0]
                matches = [cif['id']] if cif.get('id') in ids else [i for i in ids if stem == i or stem.startswith(i + '_')]
                if len(matches) != 1:
                    raise ValueError('找不到唯一对应的条目；文件名需以条目编号开头')
                document = import_input({'kind': 'cif'}, {'filename': name, 'source_base64': base64.b64encode(cif['text'].encode('utf-8')).decode()})
                db.execute('INSERT OR REPLACE INTO structures VALUES (?,?,?,?)', (matches[0], document['filename'], document['sha256'], cif['text']))
                attached += 1
            except (ValueError, ResourceValidationError) as exc:
                rejected.append({'filename': name, 'error': str(exc)})
        db.commit()
    if not attached:
        raise ResourceValidationError('没有可附加的 CIF：' + '；'.join(f"{r['filename']}：{r['error']}" for r in rejected[:3]))
    managed = _document(path)
    managed['metadata']['last_import'] = {'added': 0, 'replaced': 0, 'attached': attached, 'rejected': rejected[:20]}
    return {**value, 'managed': managed}


def stored_structure(snapshot, candidate):
    """The CIF stored with a library candidate in a run's input snapshot, or None.

    Any library file with a `structures` table qualifies, whether it is the card's own library or a mounted copy.
    """
    from .library import expand_library_slots
    from .pipeline import cif_input
    metadata = candidate.get('metadata') or {}
    entry = metadata.get('engine_reference_id')
    for item in expand_library_slots(snapshot):
        value = item['value']
        if (entry and value.get('kind') == 'library' and item['node_id'] == metadata.get('library_node_id')
                and candidate.get('node_id') == f"{item['node_id']}:{entry}"):
            try:
                with closing(sqlite3.connect(Path(value['path']).as_uri() + '?mode=ro', uri=True)) as db:
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='structures'").fetchone():
                        return None
                    row = db.execute('SELECT filename, sha256, cif FROM structures WHERE id=?', (entry,)).fetchone()
            except sqlite3.Error as exc:
                raise ValueError('无法读取自有谱库中的结构') from exc
            return cif_input(row[0], row[2], row[1]) if row else None
    return None


def list_entries(value, arguments):
    managed = value.get('managed')
    if not managed:
        return {'entries': [], 'total': 0}
    path = _library_path(managed)
    query = str(arguments.get('query', '')).strip().lower()[:100]
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT i.id, i.chemical_formula, s.metadata_json FROM id i LEFT JOIN source_cards s ON s.id=i.id ORDER BY i.id').fetchall()
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='structures'").fetchone()
        structured = {row[0] for row in db.execute('SELECT id FROM structures')} if has_table else set()
    entries = []
    for card_id, formula, metadata_json in rows:
        metadata = json.loads(metadata_json or '{}')
        if query and query not in f"{card_id} {formula} {metadata.get('name', '')}".lower():
            continue
        entries.append({'id': card_id, 'formula': formula, 'name': metadata.get('name', ''),
                        'kind': metadata.get('reference_kind', ''), 'peaks': metadata.get('peak_count', 0),
                        'structure': card_id in structured})
    return {'entries': entries[:500], 'total': len(entries)}
