"""One documentation entry point for local development and CI (no backend needed)."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from mkdocs.config import load_config
from mkdocs.structure.files import get_files


ROOT = Path(__file__).resolve().parents[1]


def navigation_pages(items):
    for item in items:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for value in item.values():
                yield from navigation_pages(value if isinstance(value, list) else [value])


def prose_lines(source):
    """Keep source line numbers while ignoring front matter and fenced examples."""
    front_matter = source.startswith('---\n')
    fence = None
    for number, line in enumerate(source.splitlines(), 1):
        if front_matter:
            if number > 1 and line == '---':
                front_matter = False
            continue
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker:
            token, tail = marker.groups()
            if fence is None:
                fence = token
                continue
            if token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = None
                continue
        if fence is None:
            yield number, line


CATALOG = json.loads((ROOT / 'docs/documentation.json').read_text(encoding='utf-8'))
LANGUAGES = CATALOG['plugins']['i18n']['languages']


def topic_path(path, languages=LANGUAGES):
    for language in languages:
        suffix = f".{language['locale']}.md"
        if path.endswith(suffix):
            return path[:-len(suffix)] + '.md'
    return path


def validate_source(path, source):
    errors = []
    headings = []
    previous = 0
    for number, line in prose_lines(source):
        heading = re.match(r'^(#{1,6})\s+\S', line)
        if heading:
            level = len(heading[1])
            headings.append(level)
            if level > previous + 1:
                errors.append(f'{path}:{number}: heading skips a level')
            previous = level
        if topic_path(path) == path:
            # Exact identifiers and links may name another language.
            # Only default English sources are checked for mixed Chinese prose.
            prose = re.sub(r'\[[^\]]*\]\([^)]*\.zh-CN\.md(?:#[^)]*)?\)', '', line)
            prose = re.sub(r'`+[^`]+`+', '', prose)
            if re.search(r'[\u3400-\u9fff]', prose):
                errors.append(f'{path}:{number}: Chinese prose belongs in a .zh-CN.md page')
    if headings.count(1) != 1:
        errors.append(f'{path}: expected exactly one # title, found {headings.count(1)}')
    return errors


def check_sources():
    config = load_config(config_file=str(ROOT / 'mkdocs.yml'))
    files = get_files(config)
    published = {file.src_uri for file in files.documentation_pages() if file.inclusion.is_included()}
    nav = list(navigation_pages(config.nav))
    errors = []
    locales = [item['locale'] for item in LANGUAGES]
    if len(locales) != len(set(locales)) or sum(bool(item.get('default')) for item in LANGUAGES) != 1:
        errors.append('documentation.json: languages need unique locales and one default')
    for path in sorted(published):
        topic = topic_path(path)
        if topic not in nav:
            errors.append(f'{path}: register its canonical topic in docs/documentation.json')
        if topic not in published:
            errors.append(f'{path}: missing default-language source {topic}')
    for path in nav:
        if topic_path(path) != path:
            errors.append(f'{path}: navigation must use the canonical topic without a language suffix')
    for path in sorted(set(nav) - published):
        errors.append(f'{path}: navigation target is missing, excluded, or has incorrect case')
    if len(nav) != len(set(nav)):
        errors.append('documentation.json: a topic is listed more than once in nav')
    for path in sorted(published):
        errors.extend(validate_source(path, (ROOT / 'docs' / path).read_text(encoding='utf-8')))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked {len(published)} source pages: inventory, headings, and language separation.', flush=True)


def run(*arguments):
    env = {**os.environ, 'PYTHONUTF8': '1'}
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'serve'])
    args = parser.parse_args()
    if args.command == 'serve':
        run('-m', 'mkdocs', 'serve')
        return
    check_sources()
    run('scripts/docs_tests.py')
    run('-m', 'mkdocs', 'build', '--strict')
    run('scripts/docs_check_site.py')


if __name__ == '__main__':
    main()
