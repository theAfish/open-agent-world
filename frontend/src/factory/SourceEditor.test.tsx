// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EditorView } from '@codemirror/view';
import { undo } from '@codemirror/commands';
import { syntaxTree } from '@codemirror/language';
import { diagnosticCount } from '@codemirror/lint';
import SourceEditor, { createSourceEditorSession } from './SourceEditor';

beforeEach(() => {
  // jsdom has no text layout; browser acceptance covers actual scrolling and keyboard input.
  Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => [] });
  Object.defineProperty(Range.prototype, 'getBoundingClientRect', { configurable: true, value: () => new DOMRect() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function view(container: HTMLElement): EditorView {
  return EditorView.findFromDOM(container.querySelector('.cm-editor')!)!;
}

it('retains independent syntax, selections and undo histories across saving, files and view unmounts, with fresh callbacks', async () => {
  const session = createSourceEditorSession();
  const onChange = vi.fn();
  const onSave = vi.fn();
  const screen = render(<SourceEditor path="behavior.py" content={'value = 1\n'} session={session} onChange={onChange} onSave={onSave} />);
  await waitFor(() => expect(syntaxTree(view(screen.container).state).toString()).toContain('AssignStatement'));
  act(() => view(screen.container).dispatch({ changes: { from: 8, to: 9, insert: '42' }, selection: { anchor: 10 } }));
  expect(onChange).toHaveBeenLastCalledWith('value = 42\n');
  // Save acknowledges the same content while temporarily disabling input.
  screen.rerender(<SourceEditor path="behavior.py" content={'value = 42\n'} session={session} onChange={onChange} onSave={onSave} readOnly />);
  screen.rerender(<SourceEditor path="behavior.py" content={'value = 42\n'} session={session} onChange={onChange} onSave={onSave} />);
  screen.rerender(<SourceEditor path="design.json" content={'{"a":1}'} session={session} onChange={onChange} onSave={onSave} />);
  act(() => view(screen.container).dispatch({ changes: { from: 5, to: 6, insert: '2' }, selection: { anchor: 6 } }));
  screen.rerender(<SourceEditor path="behavior.py" content={'value = 42\n'} session={session} onChange={onChange} onSave={onSave} />);
  expect(view(screen.container).state.selection.main.head).toBe(10);
  expect(syntaxTree(view(screen.container).state).toString()).toContain('AssignStatement');
  screen.unmount();
  const reopenedChange = vi.fn();
  const reopened = render(<SourceEditor path="behavior.py" content={'value = 42\n'} session={session} onChange={reopenedChange} onSave={onSave} />);
  expect(view(reopened.container).state.selection.main.head).toBe(10);
  act(() => { undo(view(reopened.container)); });
  expect(reopenedChange).toHaveBeenLastCalledWith('value = 1\n');
  expect(onChange).toHaveBeenCalledTimes(2);
  reopened.rerender(<SourceEditor path="design.json" content={'{"a":2}'} session={session} onChange={reopenedChange} onSave={onSave} />);
  expect(view(reopened.container).state.selection.main.head).toBe(6);
  act(() => { undo(view(reopened.container)); });
  expect(reopenedChange).toHaveBeenLastCalledWith('{"a":1}');
});

it('uses CodeMirror find and save shortcuts and enforces read-only state while an operation runs', () => {
  const session = createSourceEditorSession();
  const onChange = vi.fn();
  const onSave = vi.fn();
  const screen = render(<SourceEditor path="behavior.py" content={'value = 1\n'} session={session} onChange={onChange} onSave={onSave} />);
  const textbox = screen.getByRole('textbox', { name: 'Source code: behavior.py' });
  fireEvent.keyDown(textbox, { key: 's', code: 'KeyS', ctrlKey: true });
  expect(onSave).toHaveBeenCalledOnce();
  fireEvent.keyDown(textbox, { key: 'f', code: 'KeyF', ctrlKey: true });
  expect(screen.getByPlaceholderText('Find')).toBeTruthy();
  screen.rerender(<SourceEditor path="behavior.py" content={'value = 1\n'} session={session} onChange={onChange} onSave={onSave} readOnly />);
  expect(view(screen.container).state.readOnly).toBe(true);
  expect(textbox.getAttribute('contenteditable')).toBe('false');
  fireEvent.keyDown(textbox, { key: 's', code: 'KeyS', ctrlKey: true });
  expect(onSave).toHaveBeenCalledOnce();
});

it('applies external controlled content without echoing an edit and reveals located diagnostics', () => {
  const session = createSourceEditorSession();
  const onChange = vi.fn();
  const onSave = vi.fn();
  const screen = render(<SourceEditor path="behavior.py" content={'one\ntwo\nthree\n'} session={session} onChange={onChange} onSave={onSave} />);
  screen.rerender(<SourceEditor path="behavior.py" content={'one\nupdated\nthree\n'} session={session} onChange={onChange} onSave={onSave}
    diagnostics={[{ line: 2, column: 3, message: 'Invalid expression', severity: 'error' }]} reveal={{ line: 2, column: 3, token: 1 }} />);
  expect(onChange).not.toHaveBeenCalled();
  expect(view(screen.container).state.doc.toString()).toBe('one\nupdated\nthree\n');
  expect(view(screen.container).state.selection.main.head).toBe(6);
  expect(diagnosticCount(view(screen.container).state)).toBe(1);
  expect(screen.getByText('Ln 2, Col 3')).toBeTruthy();
});

it.each([
  ['behavior.py', 'def run():\n    return 1\n', 'FunctionDefinition'],
  ['Card.tsx', 'const Card = () => <div>Result</div>;', 'JSXElement'],
  ['card.json', '{"name":"Example"}', 'Object'],
  ['pack.toml', '[pack]\nname = "Example"\n', 'Document'],
])('loads actual syntax support for %s', async (path, content, expectedNode) => {
  const screen = render(<SourceEditor path={path} content={content} session={createSourceEditorSession()} onChange={vi.fn()} onSave={vi.fn()} />);
  await waitFor(() => expect(syntaxTree(view(screen.container).state).toString()).toContain(expectedNode));
  expect(screen.container.querySelector('.cm-lineNumbers')).toBeTruthy();
});
