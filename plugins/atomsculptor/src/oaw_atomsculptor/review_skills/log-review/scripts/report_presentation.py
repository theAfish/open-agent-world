"""Offline presentation of the existing report sequence; no scientific calculations."""
import base64
from html import escape
from pathlib import Path
import struct
import unicodedata
from urllib.parse import quote


PROVENANCE = {'historical': '已保留的历史版本', 'collection_snapshot': '采集时快照', 'missing': '历史版本缺失'}


def composition(saved):
    digits = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
    return ''.join(str(symbol) + (str(count).translate(digits) if count != 1 else '')
        for symbol, count in saved.get('elements', {}).items())


def structure_caption(item):
    saved = item.get('structure_summary', {})
    if not saved:
        return '结构元数据不可用'
    periodic = ', '.join(axis for axis, enabled in zip('xyz', saved.get('pbc', [])) if enabled)
    return f'{composition(saved)} · {saved["atom_count"]} atoms · ' + (f'PBC {periodic}' if periodic else '非周期')


def file_link(path, label):
    return '<a href="' + quote(path) + '">' + escape(str(label)) + '</a>'


def compact_story(entries):
    """Show saved states and their own recorded action; keep the ledger intact."""
    result, pending = [], []
    for entry in entries:
        if entry['kind'] == 'action':
            pending.append(entry)
            continue
        # Match native identity, never proximity. Diagnostic calls and trailing
        # unchanged validations stay in the complete report, not new figure nodes.
        action = next((item for item in reversed(pending) if item.get('run_id') == entry.get('run_id')
            and any(item.get(key) and item.get(key) == entry.get(key) for key in ('tool_id', 'command_id'))), None)
        if action:
            result.append(action)
        result.append(entry)
        pending.clear()
    return result


def decision_text(entry):
    text = entry['reasoning_summary'] or '思考摘要未保留。'
    if entry['reasoning_summary'] and entry['summary_basis'] != 'stated':
        text = ('（推断）' if entry['summary_basis'] == 'inferred' else '（依据未知）') + text
    return text


def operation_text(entry):
    status = entry['native_status']
    failed = any(word in status for word in ('failed', 'rejected', 'error')) or (
        status.startswith('exit ') and status.split(' / ')[0] != 'exit 0')
    return entry['title'] + ('（未通过，详见完整记录）' if failed else '')


def story_html(entries):
    sections = []
    for entry in compact_story(entries):
        structure = entry['kind'] == 'structure'
        if structure:
            marker = entry['title'].removeprefix('结构 ')
            body = '<div class="figure-grid' + (' multiple' if len(entry['structures']) > 1 else '') + '">'
            for number, item in enumerate(entry['structures'], 1):
                label = marker + (f'.{number}' if len(entry['structures']) > 1 else '')
                body += '<figure>'
                if item.get('preview'):
                    body += '<a class="structure-image" href="' + quote(item['preview']) + '"><img src="' + quote(item['preview']) + '" alt="' + escape(structure_caption(item), quote=True) + ' · 保存文件预览"></a>'
                else:
                    body += '<div class="preview-missing"><span>—</span><p>' + ('历史结构版本缺失' if item.get('provenance') == 'missing' else '文件预览不可用') + '</p></div>'
                body += '<figcaption>' + (file_link(item['copied_file'], '结构 ' + label) if item.get('copied_file') else escape('结构 ' + label))
                body += ' · ' + escape(PROVENANCE[item['provenance']]) + '</figcaption></figure>'
            body += '</div>'
        else:
            body = '<p>' + escape('思考：' + ' '.join(wrapped(decision_text(entry), 100, 2))) + '</p>'
            body += '<p>操作：' + escape(' '.join(wrapped(operation_text(entry), 100, 2))) + '</p>'
        sections.append('<section class="chain-step ' + entry['kind'] + '" id="' + escape(entry['id'], quote=True) + '">' + body + '</section>')
    return '<div class="chain">' + '<div class="chain-arrow" aria-hidden="true">↓</div>'.join(sections) + '</div>'


def wrapped(text, columns, limit):
    """Wrap mixed CJK/Latin SVG text by display width, with explicit ellipsis."""
    lines, line, used = [], '', 0
    for character in str(text).replace('\r', ''):
        size = 2 if unicodedata.east_asian_width(character) in {'W', 'F'} else 1
        if character == '\n' or used + size > columns:
            lines.append(line.rstrip())
            line, used = '', 0
        if character != '\n':
            line += character
            used += size
    if line or not lines:
        lines.append(line.rstrip())
    return lines[:limit-1] + [lines[limit-1].rstrip() + '…'] if len(lines) > limit else lines


def flowchart(entries, image_root=None):
    """The same minimal structure → thought/action → structure figure as HTML."""
    entries = compact_story(entries)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="HEIGHT" viewBox="0 0 900 HEIGHT">',
        '<title>结构演变</title><desc>图片为保存文件预览，箭头仅表示记录顺序。</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="Arial, PingFang SC, Microsoft YaHei, sans-serif" fill="#263b45">']
    mermaid, y = ['flowchart TD'], 24

    def text_lines(lines, x, top, size=16, color='#263b45'):
        for offset, line in enumerate(lines):
            parts.append(f'<text x="{x}" y="{top+offset*25}" font-size="{size}" fill="{color}">{escape(line)}</text>')

    for index, entry in enumerate(entries):
        parts.append('<g><title>' + escape(entry['title']) + '</title>')
        if entry['kind'] == 'structure':
            items = entry.get('structures', [])
            columns = min(2, max(1, len(items)))
            width = 800 / columns
            image_height = 300 if columns == 1 else 240
            tile_height = image_height + 40
            height = max(1, (len(items)+columns-1)//columns)*tile_height
            for number, item in enumerate(items):
                left = 50 + number % columns * width
                top = y + number // columns * tile_height
                if item.get('preview'):
                    href = quote(item['preview'])
                    fitted_width, fitted_height = width-24, image_height-12
                    if image_root is not None:
                        path = (Path(image_root) / item['preview']).resolve()
                        path.relative_to(Path(image_root).resolve())
                        data = path.read_bytes()
                        pixel_width, pixel_height = struct.unpack('>II', data[16:24])
                        scale = min(fitted_width/pixel_width, fitted_height/pixel_height)
                        fitted_width, fitted_height = pixel_width*scale, pixel_height*scale
                        href = 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
                    image_x, image_y = left+(width-fitted_width)/2, top+(image_height-fitted_height)/2
                    parts.append(f'<image href="{href}" x="{image_x}" y="{image_y}" width="{fitted_width}" height="{fitted_height}" preserveAspectRatio="xMidYMid meet"/>')
                else:
                    text_lines(['历史版本缺失' if item['provenance'] == 'missing' else '文件预览不可用'], left+24, top+image_height/2, 15, '#6a7c86')
                label = entry['title'] + (f'.{number+1}' if len(items) > 1 else '')
                text_lines(wrapped(label+' · '+PROVENANCE[item['provenance']], int((width-24)/7), 1), left+12, top+image_height+22, 13, '#6a7c86')
        else:
            summary = wrapped('思考：'+decision_text(entry), 92, 2)
            operation = wrapped('操作：'+operation_text(entry), 92, 2)
            text_lines(summary, 72, y+22)
            text_lines(operation, 72, y+30+len(summary)*25)
            height = (len(summary)+len(operation))*25+22
        parts.append('</g>')
        y += height
        if index < len(entries)-1:
            parts.append(f'<path d="M450,{y+5} V{y+33} M446,{y+28} L450,{y+33} L454,{y+28}" fill="none" stroke="#b5c5cd" stroke-dasharray="3 3"/>')
            y += 46
        label = entry['title'].replace('"', "'").replace('\n', ' ').replace('[', '(').replace(']', ')')
        mermaid.append(f'  N{index}["{label}"]')
        if index:
            mermaid.append(f'  N{index-1} -.-> N{index}')
    parts += [f'<text x="50" y="{y+24}" font-size="11" fill="#6a7c86">保存文件预览 · 箭头仅表示记录顺序</text>', '</g></svg>']
    return ''.join(parts).replace('HEIGHT', str(y+44)), '\n'.join(mermaid)+'\n'
