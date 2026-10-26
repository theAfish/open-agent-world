import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { basicSetup } from 'codemirror';
import { Annotation, Compartment, EditorSelection, EditorState, Prec, Transaction, type Extension } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { indentWithTab } from '@codemirror/commands';
import { HighlightStyle, indentUnit, StreamLanguage, syntaxHighlighting } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { openSearchPanel } from '@codemirror/search';
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint';
import './sourceEditor.css';

export interface SourceDiagnostic {
  line?: number;
  column?: number;
  severity?: string;
  message: string;
}

type SavedDocument = { state: EditorState; scroll: ReturnType<EditorView['scrollSnapshot']> };
export type SourceEditorSession = Map<string, SavedDocument>;

/** Keep this session in the project workspace, so switching views retains each file's history. */
export function createSourceEditorSession(): SourceEditorSession { return new Map(); }

export interface SourceEditorProps {
  path: string;
  documentKey?: string;
  content: string;
  onChange: (content: string) => void;
  onSave: () => void | Promise<void>;
  session: SourceEditorSession;
  readOnly?: boolean;
  diagnostics?: readonly SourceDiagnostic[];
  reveal?: { line: number; column?: number; token?: number };
}

const externalChange = Annotation.define<boolean>();
const language = new Compartment();
const editing = new Compartment();
const listeners = new Compartment();
const emptyDiagnostics: readonly SourceDiagnostic[] = [];

// CSS tokens follow OAW's live document theme without recreating editor state.
// Installing a non-fallback highlighter replaces basicSetup's light-only colors.
const sourceHighlighting = syntaxHighlighting(HighlightStyle.define([
  { tag: tags.keyword, class: 'oaw-code-keyword' },
  { tag: [tags.string, tags.regexp, tags.inserted], class: 'oaw-code-string' },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], class: 'oaw-code-number' },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName), tags.definition(tags.variableName)], class: 'oaw-code-function' },
  { tag: [tags.typeName, tags.className, tags.namespace, tags.annotation, tags.macroName], class: 'oaw-code-type' },
  { tag: [tags.propertyName, tags.attributeName, tags.tagName], class: 'oaw-code-property' },
  { tag: [tags.comment, tags.meta], class: 'oaw-code-comment' },
  { tag: [tags.escape, tags.special(tags.string)], class: 'oaw-code-number' },
  { tag: [tags.invalid, tags.deleted], class: 'oaw-code-invalid' },
  { tag: tags.heading, class: 'oaw-code-heading' },
  { tag: tags.link, class: 'oaw-code-link' },
  { tag: tags.strong, class: 'oaw-code-strong' },
  { tag: tags.emphasis, class: 'oaw-code-emphasis' },
  { tag: tags.strikethrough, class: 'oaw-code-strike' },
]));

function languageName(path: string): string {
  const extension = path.split('.').at(-1)?.toLowerCase();
  return ({ py: 'Python', pyi: 'Python', ts: 'TypeScript', tsx: 'TSX', js: 'JavaScript', jsx: 'JSX',
    mjs: 'JavaScript', cjs: 'JavaScript', json: 'JSON', toml: 'TOML', css: 'CSS', html: 'HTML',
    htm: 'HTML', md: 'Markdown', markdown: 'Markdown', yaml: 'YAML', yml: 'YAML', sh: 'Shell',
  } as Record<string, string>)[extension ?? ''] ?? 'Plain text';
}

async function loadLanguage(path: string): Promise<Extension> {
  const extension = path.split('.').at(-1)?.toLowerCase();
  switch (extension) {
    case 'py': case 'pyi': return (await import('@codemirror/lang-python')).python();
    case 'ts': case 'tsx': case 'js': case 'jsx': case 'mjs': case 'cjs':
      return (await import('@codemirror/lang-javascript')).javascript({ typescript: extension.startsWith('ts'), jsx: extension.endsWith('x') });
    case 'json': return (await import('@codemirror/lang-json')).json();
    case 'toml': return StreamLanguage.define((await import('@codemirror/legacy-modes/mode/toml')).toml);
    case 'yaml': case 'yml': return StreamLanguage.define((await import('@codemirror/legacy-modes/mode/yaml')).yaml);
    case 'sh': return StreamLanguage.define((await import('@codemirror/legacy-modes/mode/shell')).shell);
    case 'css': return (await import('@codemirror/lang-css')).css();
    case 'html': case 'htm': return (await import('@codemirror/lang-html')).html();
    case 'md': case 'markdown': return (await import('@codemirror/lang-markdown')).markdown();
    default: return [];
  }
}

const editorTheme = EditorView.theme({
  '&': { height: '100%', fontSize: '12px', color: 'var(--ink, #344139)', backgroundColor: 'var(--card-surface, #fff)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { overflow: 'auto', fontFamily: 'Consolas, "SFMono-Regular", monospace', lineHeight: '1.65' },
  '.cm-content': { padding: '12px 0', caretColor: 'var(--ink, #344139)' },
  '.cm-line': { padding: '0 14px 0 8px' },
  '.cm-gutters': { backgroundColor: 'var(--surface, #f6f7f4)', color: 'var(--muted, #7c847e)', borderRight: '1px solid var(--line, #dfe3da)' },
  '.cm-lineNumbers .cm-gutterElement': { minWidth: '34px', padding: '0 8px' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--accent, #617b72) 7%, transparent)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'color-mix(in srgb, var(--accent, #617b72) 23%, transparent)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--ink, #344139)' },
  '.cm-panels': { backgroundColor: 'var(--surface, #f6f7f4)', color: 'var(--ink, #344139)', fontSize: '12px' },
  '.cm-search': { padding: '8px 12px' },
  '.cm-search label': { display: 'inline-flex', alignItems: 'center', gap: '4px', marginRight: '8px' },
  '.cm-textfield': { background: 'var(--card-surface, #fff)', color: 'inherit', border: '1px solid var(--line, #dfe3da)', borderRadius: '4px' },
  '.cm-button': { backgroundImage: 'none', backgroundColor: 'var(--card-surface, #fff)', color: 'inherit', border: '1px solid var(--line, #dfe3da)', borderRadius: '4px' },
  '.cm-tooltip': { backgroundColor: 'var(--card-surface, #fff)', color: 'var(--ink, #344139)', borderColor: 'var(--line, #dfe3da)' },
});

function editability(readOnly: boolean): Extension {
  return [EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)];
}

function position(state: EditorState, line: number, column = 1): number {
  const target = state.doc.line(Math.max(1, Math.min(state.doc.lines, Math.floor(line) || 1)));
  return Math.min(target.to, target.from + Math.max(0, (Math.floor(column) || 1) - 1));
}

function syncContent(state: EditorState, content: string): Transaction {
  return state.update({
    changes: { from: 0, to: state.doc.length, insert: content },
    selection: EditorSelection.create(state.selection.ranges.map(range => EditorSelection.range(
      Math.min(range.anchor, content.length), Math.min(range.head, content.length),
    )), state.selection.mainIndex),
    annotations: [externalChange.of(true), Transaction.addToHistory.of(false)],
  });
}

export default function SourceEditor({ path, documentKey = path, content, onChange, onSave, session,
  readOnly = false, diagnostics = emptyDiagnostics, reveal }: SourceEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView>();
  const callbacks = useRef({ onChange, onSave, readOnly });
  callbacks.current = { onChange, onSave, readOnly };
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const [syntaxFailed, setSyntaxFailed] = useState(false);

  useLayoutEffect(() => {
    if (!host.current) return;
    const reportCursor = (state: EditorState) => {
      const line = state.doc.lineAt(state.selection.main.head);
      setCursor({ line: line.number, column: state.selection.main.head - line.from + 1 });
    };
    const events = [
      EditorView.updateListener.of(update => {
        if (update.docChanged && update.transactions.some(transaction => transaction.docChanged && !transaction.annotation(externalChange))) {
          callbacks.current.onChange(update.state.doc.toString());
        }
        if (update.selectionSet || update.docChanged) reportCursor(update.state);
      }),
      Prec.highest(keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => {
        if (!callbacks.current.readOnly) void callbacks.current.onSave();
        return true;
      } }])),
    ];
    const saved = session.get(documentKey);
    let state = saved?.state ?? EditorState.create({ doc: content, extensions: [
      basicSetup, sourceHighlighting, editorTheme, lintGutter(), keymap.of([indentWithTab]),
      indentUnit.of(/\.pyi?$/.test(path) ? '    ' : '  '), EditorState.tabSize.of(4),
      EditorView.contentAttributes.of({ 'aria-label': `Source code: ${path}`, 'aria-multiline': 'true', spellcheck: 'false' }),
      language.of([]), editing.of(editability(readOnly)), listeners.of(events),
    ] });
    const matchingContent = state.doc.toString() === content;
    if (!matchingContent) state = syncContent(state, content).state;
    if (saved) state = state.update({ effects: [listeners.reconfigure(events), editing.reconfigure(editability(readOnly))] }).state;
    const view = new EditorView({ state, parent: host.current, scrollTo: matchingContent ? saved?.scroll : undefined });
    editor.current = view;
    reportCursor(state);
    setSyntaxFailed(false);
    let active = true;
    void loadLanguage(path).then(extension => {
      if (active) view.dispatch({ effects: language.reconfigure(extension) });
    }).catch(() => { if (active) setSyntaxFailed(true); });
    return () => {
      active = false;
      session.set(documentKey, { state: view.state, scroll: view.scrollSnapshot() });
      view.destroy();
      editor.current = undefined;
    };
    // A document owns its EditorState. Prop edits and read-only changes use transactions below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentKey, path, session]);

  useEffect(() => {
    const view = editor.current;
    if (view && view.state.doc.toString() !== content) view.dispatch(syncContent(view.state, content));
  }, [content, documentKey]);

  useEffect(() => {
    editor.current?.dispatch({ effects: editing.reconfigure(editability(readOnly)) });
  }, [readOnly, documentKey]);

  useEffect(() => {
    const view = editor.current;
    if (!view) return;
    const items: Diagnostic[] = diagnostics.map(item => {
      const from = position(view.state, item.line ?? 1, item.column);
      return { from, to: Math.max(from, view.state.doc.lineAt(from).to), message: item.message,
        severity: item.severity === 'warning' || item.severity === 'info' || item.severity === 'hint' ? item.severity : 'error' };
    });
    view.dispatch(setDiagnostics(view.state, items));
  }, [diagnostics, documentKey]);

  useEffect(() => {
    const view = editor.current;
    if (!view || !reveal) return;
    const anchor = position(view.state, reveal.line, reveal.column);
    view.dispatch({ selection: { anchor }, effects: EditorView.scrollIntoView(anchor, { y: 'center' }) });
    view.focus();
  }, [documentKey, reveal?.line, reveal?.column, reveal?.token]);

  return <div className="pack-source-editor" data-file-path={path} data-language={languageName(path)}>
    <div className="pack-source-editor-host" ref={host} />
    <div className="pack-source-editor-status">
      <span>{languageName(path)}{syntaxFailed ? ' · Syntax unavailable' : ''}</span>
      <span>Ln {cursor.line}, Col {cursor.column}</span>
      <span>{/\.pyi?$/.test(path) ? '4' : '2'} spaces</span>
      {readOnly && <span>Read only</span>}
      <button type="button" onClick={() => { if (editor.current) openSearchPanel(editor.current); }} title="Find: Ctrl/Cmd+F · Save: Ctrl/Cmd+S · Leave editor: Escape, then Tab">Find in file</button>
    </div>
  </div>;
}
