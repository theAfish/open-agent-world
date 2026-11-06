"""Bounded local PDF/Excel conversion in a disposable interpreter, without credentials."""
from contextlib import redirect_stdout
import json
import sys


def main():
    request = json.loads(sys.stdin.buffer.read())
    # The host supplies its already selected adapter paths. -I -S excludes
    # user site hooks, project imports, and PYTHONPATH from process startup.
    sys.path[:] = request['paths']
    try:
        with redirect_stdout(sys.stderr):
            from oaw_knowledge_base.markdown import _pymupdf_markdown_local, _xlsx_markdown_local, _truncate
            convert = {'pdf': _pymupdf_markdown_local, 'xlsx': _xlsx_markdown_local}[request['kind']]
            text, _, _ = _truncate(convert(request['path']), request['kind'], '')
        response = {'ok': True, 'text': text}
    except Exception as error:
        response = {'ok': False, 'error': str(error)}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
