"""Read-only QualX/COD reference library adapter; no vendor code or data bundled."""
import hashlib
import json
import os
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import re

ELEMENTS = set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split())

def library_catalog(value, arguments):
    """Only the operator's environment can select the hosted library catalog."""
    from open_agent_world.plugin_api import ResourceValidationError
    location = os.environ.get('OAW_XRD_LIBRARY_CATALOG')
    if location is None:
        return {'managed': False, 'libraries': []}
    try:
        entries = json.loads(Path(location).read_text(encoding='utf-8'))
        if not isinstance(entries, dict):
            raise ValueError('谱库目录格式错误')
        libraries = []
        for key, entry in entries.items():
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', key) or not isinstance(entry, dict):
                raise ValueError('谱库目录格式错误')
            path = Path(entry['path'])
            if not path.is_absolute() or path.suffix != '.sq' or not isinstance(entry['label'], str):
                raise ValueError('谱库目录格式错误')
            libraries.append({'id': key, 'label': entry['label'], 'path': str(path), 'filename': path.name})
        return {'managed': True, 'libraries': libraries}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ResourceValidationError('无法读取已部署谱库目录，请联系托管管理员') from exc


def configured_library_path(arguments):
    from open_agent_world.plugin_api import ResourceValidationError
    catalog = library_catalog({}, {})
    if catalog['managed']:
        if 'path' not in arguments:
            for entry in catalog['libraries']:
                if entry['id'] == arguments.get('library_id'):
                    return entry['path']
        raise ResourceValidationError('请选择已部署的谱库')
    if 'library_id' in arguments:
        raise ResourceValidationError('未配置已部署谱库目录')
    return arguments.get('path', '')


def allowed_elements(text):
    symbols = set(re.split(r'[,，;\s]+', text.strip())) - {''}
    if not symbols.issubset(ELEMENTS):
        raise ValueError('元素条件请使用空格或逗号分隔的正确元素符号，例如 Li Ti P O')
    return symbols


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def inspect_library(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('请选择 QualX / POW_COD 的 .sq 主数据库文件')
    with closing(connect(path)) as db:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(id)')}
        if not {'id', 'chemical_formula', 'dvalue', 'intensita'}.issubset(columns):
            raise ValueError('不支持的数据库结构：需要 QualX / POW_COD SQLite 峰表；不能直接读取 ICDD 二进制库')
        count = db.execute('SELECT count(*) FROM id').fetchone()[0]
        info = dict(db.execute('SELECT * FROM infodb LIMIT 1').fetchone() or {})
    if not count:
        raise ValueError('数据库为空')
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            digest.update(block)
    stat = path.stat()
    return {'kind': 'library', 'filename': path.name, 'path': str(path),
            'sha256': digest.hexdigest(), 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'count': count, 'metadata': {'source': str(info.get('source') or 'QualX / POW_COD'), 'version': str(info.get('date', '')),
                'type': str(info.get('type', '')), 'reference_kind': 'card_export' if info.get('type') == 'PDF2' else 'calculated'}, 'points': [], 'peaks': []}


def validate_library(doc):
    if doc.get('managed'):
        validate_library(doc['managed'])
        if not doc.get('path') and not doc.get('slots'):
            return
    if doc.get('slots'):
        for mounted in doc['slots']:
            if mounted:
                validate_library(mounted)
        return
    path = Path(doc.get('path', ''))
    if not path.is_file():
        raise ValueError('参考谱库文件不存在，请在谱库卡片中配置本机 .sq 路径')
    if doc.get('metadata', {}).get('managed'):
        return
    stat = path.stat()
    if stat.st_size != doc.get('size_bytes') or stat.st_mtime_ns != doc.get('mtime_ns'):
        raise ValueError('谱库文件已变更，请重新点击「加载谱库」确认版本')


def expand_library_slots(inputs):
    result = []
    for item in inputs:
        slots = item['value'].get('slots')
        managed = item['value'].get('managed')
        if item['value']['kind'] == 'library' and (slots or managed):
            if managed and managed.get('count'):
                result.append({**item, 'node_id': f"{item['node_id']}:managed", 'value': managed})
            for index, mounted in enumerate(slots or []):
                if mounted:
                    result.append({**item, 'node_id':f"{item['node_id']}:slot{index+1}", 'value':mounted})
        else:
            result.append(item)
    return result


def iter_references(item, allowed=None, reference_ids=None):
    """Stream every record. Native intensities may peak at 1000, not 100."""
    import numpy as np
    doc = item['value']
    validate_library(doc)
    with closing(connect(doc['path'])) as db:
        tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources = None
        if 'source_cards' in tables:
            query = ('SELECT m.engine_id,s.id,s.metadata_json FROM card_id_map m JOIN source_cards s ON s.id=m.pdf_card_id'
                     if 'card_id_map' in tables else 'SELECT id,id,metadata_json FROM source_cards')
            sources = {str(row[0]): (row[1], json.loads(row[2])) for row in db.execute(query)}
            info = dict(db.execute('SELECT * FROM infodb LIMIT 1').fetchone() or {})
            structured = ({str(r[0]) for r in db.execute('SELECT id FROM structures')}
                          if 'structures' in tables else set())
        # Filter small metadata first: rejected entries must not load their large
        # peak arrays. Keep ID traversal order and the exact existing ranking.
        if reference_ids is None:
            query = ('SELECT rowid AS record_rowid, id, chemical_formula FROM id ORDER BY id'
                     if allowed else 'SELECT id, chemical_formula, dvalue, intensita FROM id ORDER BY id')
            rows = db.execute(query)
        else:
            # Only read the records returned by the connected library's search
            # process. Parameter binding also handles non-numeric reference IDs.
            ids = sorted(set(reference_ids))
            def selected_rows():
                for start in range(0, len(ids), 400):
                    batch = ids[start:start + 400]
                    placeholders = ','.join('?' for _ in batch)
                    found = list(db.execute(
                        f'SELECT rowid AS record_rowid, id, chemical_formula, dvalue, intensita FROM id WHERE id IN ({placeholders}) ORDER BY id', batch))
                    if {str(row['id']) for row in found} != set(batch):
                        raise ValueError('QualX 返回的候选不属于当前连接的谱库，请检查数据库配置')
                    yield from found
            rows = selected_rows()
        for row in rows:
            if allowed and not set(re.findall(r'[A-Z][a-z]?',row['chemical_formula'] or '')).issubset(allowed):
                yield {'excluded':True}
                continue
            if allowed and reference_ids is None:
                row = db.execute('SELECT id, chemical_formula, dvalue, intensita FROM id WHERE rowid=?',
                                 (row['record_rowid'],)).fetchone()
            def numbers(value):
                if isinstance(value, bytes):
                    value = value.decode('ascii')
                return np.fromstring(str(value or '').strip().rstrip(','), sep=',')
            try:
                d = numbers(row['dvalue'])
                intensity = numbers(row['intensita'])
            except (ValueError, UnicodeError):
                yield {'invalid_record':str(row['id'])}
                continue
            if len(d) != len(intensity) or not len(d) or not np.isfinite(d).all() or not np.isfinite(intensity).all() or (d <= 0).any() or (intensity < 0).any() or intensity.max() <= 0:
                yield {'invalid_record':str(row['id'])}
                continue
            intensity = intensity / intensity.max() * 100
            metadata = {'formula': row['chemical_formula'] or '', 'reference_code': str(row['id']),
                'source': doc['metadata']['source'], 'library_node_id': item['node_id'],
                'reference_kind': 'calculated', 'url': f"https://www.crystallography.net/cod/{row['id']}.html"}
            filename = f"COD {row['id']} · {row['chemical_formula']}"
            if sources is not None:
                if str(row['id']) not in sources:
                    raise ValueError('谱库卡号与来源归档不一致，请检查补充库')
                card_id, source = sources[str(row['id'])]
                filename = f"{card_id} · {row['chemical_formula']}"
                metadata = {'formula': row['chemical_formula'] or '', 'reference_code': card_id,
                    'engine_reference_id': str(row['id']), 'library_node_id': item['node_id'],
                    'source': str(info.get('source') or 'User TXT cards'),
                    'reference_kind': source.get('reference_kind', 'card_export'),
                    'structure_available': 'true' if str(row['id']) in structured else 'false'}
            yield {'node_id': f"{item['node_id']}:{row['id']}", 'revision': item['revision'],
                'value': {'filename': filename, 'metadata': metadata,
                    '_d': d, '_intensity': intensity}}
