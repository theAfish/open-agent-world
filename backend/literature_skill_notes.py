"""Readable, source-bound Skill objects beside each contributing Paper."""
from hashlib import sha256
from urllib.parse import quote
from contextlib import contextmanager
from contextvars import ContextVar

from backend.world.models import CardCreate, CardPatch


_projection = ContextVar('literature_skill_projection', default=None)


@contextmanager
def skill_note_projection(services):
    """Compensate managed note bytes when the enclosing database work fails.

    The caller holds the node mutation lock. An external filesystem edit is
    preserved: compensation only touches bytes last written by this projection.
    """
    active = _projection.get()
    if active is not None:
        yield active
        return
    state = {'undo': {}, 'changed': set()}
    token = _projection.set(state)
    try:
        yield state
    except BaseException:
        for path, (before, written_digest) in reversed(list(state['undo'].items())):
            if not path.exists() or sha256(path.read_bytes()).hexdigest() != written_digest:
                continue
            if before is None:
                path.unlink()
            else:
                services.resources._write_managed(path, before)
        raise
    finally:
        _projection.reset(token)


def remember_note_write(services, node_id, content, *, created=False):
    state = _projection.get()
    if state is None:
        raise RuntimeError('Skill note writes require a projection transaction')
    record = services.resources.get_record(node_id)
    path = services.resources.resolve_relative_path(record.relative_path, require_exists=True)
    previous = state['undo'].get(path)
    before = previous[0] if previous is not None else None if created else path.read_bytes()
    state['undo'][path] = (before, sha256(content.encode()).hexdigest())
    state['changed'].add(node_id)


async def publish_skill_resources(services, state):
    for node_id in state['changed']:
        await services._publish_resource_modified(services.resources.read_text(node_id),
            agent_id=None, operation='skill_projection')


def skill_note(scope_id, method, paper_id, *, micro=False):
    sources = [source for source in method['sources'] if source['paper_id'] == paper_id]
    status = {'draft': '草稿 · 尚未验证', 'executable': '可执行 · 尚未科学验证', 'validated': '已有验证记录'}.get(method.get('status'), '草稿 · 尚未验证')
    if micro:
        status = '摘要研读草稿' if any(source.get('basis') == 'abstract' for source in sources) else '题录策略草稿'
    lines = [f"# {method['name']}", '', f"Skill · r{method['revision']} · {status}", '', method['purpose'], '', '## 步骤']
    for number, step in enumerate(method.get('steps', []), 1):
        label = '研究策略' if micro else '原文方法' if step.get('origin') == 'source' else '工程改编'
        lines.append(f"{number}. [{label}] {step['instruction']}")
    if not method.get('steps'): lines.append('尚未整理步骤。')
    for title, key in [('适用条件', 'preconditions'), ('限制', 'constraints'), ('来源局限', 'limitations'), ('待补充', 'missing')]:
        if method.get(key): lines += ['', f'## {title}', *[f'- {item}' for item in method[key]]]
    lines += ['', '## 本篇来源']
    for source in sources:
        if micro:
            basis = '来源摘要' if source.get('basis') == 'abstract' else '题录'
            lines += [f"- 来源层级：{basis} · {source.get('title', '')}"]
            if source.get('doi'): lines.append(f"- DOI：{source['doi']}")
            if source.get('source_url'): lines.append(f"- 来源网址：{source['source_url']}")
            if source.get('metadata_sha256'): lines.append(f"- 题录快照：{source['metadata_sha256']}")
            if source.get('quote'): lines.append(f"> {source['quote'].replace(chr(10), chr(10)+'> ')}")
        else:
            lines += [f"- p{source['page']} · 文档 {source['document_version_id'][:12]}", f"> {source['quote'].replace(chr(10), chr(10)+'> ')}"]
    if micro:
        lines += ['', '仅基于所列题录或来源摘要整理研究策略；未读取全文，不代表已提取、执行或验证论文方法。']
    lines += ['', f"提取者：{method.get('recorded_by', '未记录')}"]
    if not micro:
        lines.append(f"[导出 Skill 包](/api/literature/scopes/{quote(scope_id, safe='')}/methods/{quote(method['id'], safe='')}/export)")
    return '\n'.join(lines)


async def materialize_skill_notes(services, scope, value, cards, created, updated):
    """Refresh unedited generated text only; retain user edits and dragged positions."""
    notes = {}
    per_paper = {}
    entries = [(method['id'], False, method) for method in value['methods']] + [
        (item['id'], True, item) for item in value.get('micro_skills', [])]
    active_sources = {(identifier, source['paper_id']) for identifier, _, method in entries for source in method['sources']}
    for node in list(cards.values()):
        if (node.type == 'text' and node.config.get('research_projection') == 'paper_skill'
                and node.config.get('scope_id') == scope.id
                and (node.config.get('method_id'), node.config.get('paper_id')) not in active_sources
                and not node.config.get('projection_historical')):
            # Retire the association, never the user's resource or its text.
            node = services.world.update_card(node.id, CardPatch(config={**node.config,
                'projection_historical': True}, expected_revision=node.revision))
            cards[node.id] = node; updated.append(node)
    for identifier, micro, method in entries:
        for paper_id in dict.fromkeys(source['paper_id'] for source in method['sources']):
            paper = cards.get(paper_id)
            if not paper or paper.type != 'library.paper': continue
            content = skill_note(scope.id, method, paper_id, micro=micro)
            digest = sha256(content.encode()).hexdigest()
            node = next((card for card in cards.values() if card.type == 'text'
                and card.config.get('research_projection') == 'paper_skill'
                and card.config.get('scope_id') == scope.id and card.config.get('method_id') == identifier
                and card.config.get('paper_id') == paper_id), None)
            count = per_paper.get(paper_id, 0); per_paper[paper_id] = count + 1
            config = {'filename': f"{'micro-' if micro else ''}{method['id']}-skill.md", 'research_projection': 'paper_skill',
                'scope_id': scope.id, 'method_id': identifier, 'paper_id': paper_id,
                'entity_id': 'micro_skill:' + identifier if micro else 'method:' + method['id'], 'method_revision': method['revision'], 'generated_sha256': digest,
                'projection_kind': 'micro_skill' if micro else 'method',
                'projection_historical': False}
            if node is None:
                node = await services._create_card(CardCreate(type='text', name=('Skill · ' + method['name'])[:200],
                    parent_id=None, position={'x': paper.position.x + paper.size.width + 70, 'y': paper.position.y + count * 320},
                    size={'width': 300, 'height': 260}, config=config, content=content), _publish_event=False)
                remember_note_write(services, node.id, content, created=True)
                node = services.world.update_card(node.id, CardPatch(config={**node.config,
                    'revision': services.resources.get_record(node.id).revision}, expected_revision=node.revision))
                cards[node.id] = node; created.append(node)
            else:
                old = services.resources.read_text(node.id)
                edited = sha256(old.content.encode()).hexdigest() != node.config.get('generated_sha256')
                if not edited and old.content != content:
                    remember_note_write(services, node.id, content)
                    old = services.resources.replace_text(node.id, content, expected_revision=old.revision, operation='skill_projection')
                next_config = {**node.config, **config, 'projection_has_user_edits': edited, 'revision': old.revision}
                if edited:
                    next_config['generated_sha256'] = node.config.get('generated_sha256')
                    next_config['method_revision'] = node.config.get('method_revision')
                    next_config['available_method_revision'] = method['revision']
                if next_config != node.config:
                    node = services.world.update_card(node.id, CardPatch(config=next_config, expected_revision=node.revision))
                    cards[node.id] = node; updated.append(node)
            notes.setdefault(identifier, node.id)
    return notes
