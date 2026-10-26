// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { PackIDE, initialInterface } from './PackIDE';
import { PackDesigner } from './FactoryViews';
import type { PluginViewProps } from '../plugins/sdk';
import { useLocale } from '../i18n';
import { surfaceDraftKey, useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { PackProject, ProjectCard } from './projectTypes';

vi.mock('./SourceEditor', () => ({ createSourceEditorSession: () => new Map(), default: ({ content, onChange }: { content: string; onChange: (value: string) => void }) => <textarea aria-label="Source code" value={content} onChange={event => onChange(event.target.value)} /> }));
vi.mock('./PackVisualEditor', () => ({ PackVisualEditor: ({ design, onChange }: { design: import('./projectTypes').CardInterface; onChange: (value: import('./projectTypes').CardInterface) => void }) => <><label>Output template<input value={design.function.template} onChange={event => onChange({ ...design, function: { ...design.function, template: event.target.value } })} /></label><label>Operation<select value={design.function.operation} onChange={event => onChange({ ...design, function: { ...design.function, operation: event.target.value as 'sum' } })}><option value="template">Template</option><option value="sum">Sum</option></select></label><input aria-label="Field type" /></> }));
const card: ProjectCard = { id: 'team.example.card', label: 'Example', kind: 'declarative', design_path: 'designs/card.json', design: null };
const design = initialInterface(card);
design.function = { fields: [{ key: 'name', label: 'Name', type: 'text', default: 'World', required: false }], operation: 'template', template: 'Hello {{name}}', separator: '\n' };
const hash = 'a'.repeat(64);
const project: PackProject = { id: 'project', path: '/projects/example', name: 'Example', distribution_id: 'team.example', version: '0.1.0', revision: hash,
  files: [{ path: 'designs/card.json', size_bytes: 100, sha256: hash, editable: true }, { path: 'src/behavior.py', size_bytes: 20, sha256: hash, editable: true }],
  cards: [{ ...card, design }], spec: {}, diagnostics: [] };
beforeEach(() => {
  vi.spyOn(worldApi, 'listPackProjects').mockResolvedValue({ projects: [], default_directory: '/projects' });
  vi.spyOn(worldApi, 'createPackProject').mockResolvedValue(project);
  vi.spyOn(worldApi, 'getPackProject').mockResolvedValue(project);
  vi.spyOn(worldApi, 'checkPackProject').mockResolvedValue({ ok: true, diagnostics: [], level: 'static' });
  vi.spyOn(worldApi, 'readPackProjectFile').mockImplementation(async (_id, filePath) => ({ path: filePath,
    content: filePath.endsWith('.json') ? JSON.stringify(design, null, 2) + '\n' : 'handwritten = True\n', sha256: hash }));
});
afterEach(() => { cleanup(); useNodeSurfaceStore.getState().setDraft('pack-ide-test-draft', ''); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function opened() {
  const screen = render(<PackIDE />);
  fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
  await screen.findByRole('button', { name: 'Function' });
  return screen;
}

it('creates a valid default ID for names starting with numbers and retains an explicitly chosen namespace', async () => {
  const screen = render(<PackIDE />);
  fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '2026 Notes' } });
  expect((screen.getByLabelText('Pack ID') as HTMLInputElement).value).toBe('local.card-2026-notes');
  fireEvent.change(screen.getByLabelText('Pack ID'), { target: { value: 'team.notes' } });
  fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Renamed Notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
  await screen.findByRole('button', { name: 'Function' });
  expect(worldApi.createPackProject).toHaveBeenCalledWith({ name: 'Renamed Notes', distribution_id: 'team.notes' });
});

it('saves validated visual logic with the loaded project revision, leaving handwritten source untouched', async () => {
  const write = vi.spyOn(worldApi, 'writePackProjectFile');
  const save = vi.spyOn(worldApi, 'savePackProjectDesign').mockResolvedValue(project);
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText(/Output template/), { target: { value: 'Welcome {{name}}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Project files saved.');
  expect(write).not.toHaveBeenCalled();
  expect(save).toHaveBeenCalledOnce();
  expect(save.mock.calls[0][1]).toMatchObject({ card_id: card.id, expected_revision: hash,
    design: { function: { template: 'Welcome {{name}}' } } });
});

it('keeps the visual draft editable when validation rejects a function', async () => {
  vi.spyOn(worldApi, 'savePackProjectDesign').mockRejectedValue(new Error('Sum requires a numeric field'));
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'sum' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Sum requires a numeric field');
  expect((screen.getByLabelText('Operation') as HTMLSelectElement).value).toBe('sum');
  expect(screen.getByLabelText('Field type')).toBeTruthy();
});

it('retains unsaved source edits when a Factory workspace closes and reopens', async () => {
  const screen = render(<PackIDE draftKey="pack-ide-test-draft" />);
  fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
  fireEvent.click(await screen.findByRole('button', { name: 'src/behavior.py' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Source code' }), { target: { value: 'retained_draft = True\n' } });
  screen.unmount();
  const reopened = render(<PackIDE draftKey="pack-ide-test-draft" />);
  expect((reopened.getByRole('textbox', { name: 'Source code' }) as HTMLTextAreaElement).value).toBe('retained_draft = True\n');
});

it('opens the experimental IDE as a secondary Factory tool and retains both packaging and project drafts', async () => {
  const key = surfaceDraftKey('pack-ide-test-factory', 'pack-project');
  const props = { level: 'workspace', card: { id: 'pack-ide-test-factory', config: {
    id: 'team.example', name: 'Packaging', version: '0.1.0',
    creator: { packaging: 'standard', accent_color: '#617b66', author: '', description: '', preparation: '', example: '', expected_result: '' },
  } }, host: { updateConfig: vi.fn() } } as unknown as PluginViewProps;
  const screen = render(<PackDesigner {...props} />);
  expect(screen.queryByRole('region', { name: 'Pack IDE' })).toBeNull();
  fireEvent.change(screen.getByLabelText('卡包名称'), { target: { value: 'Packaging draft' } });
  screen.getByText('Developer tools').closest('details')!.open = true;
  fireEvent.click(screen.getByRole('button', { name: 'Pack IDE Experimental' }));
  const ide = within(screen.getByRole('dialog', { name: 'Pack IDE' }));
  fireEvent.click(ide.getByRole('button', { name: 'Create project' }));
  fireEvent.click(await ide.findByRole('button', { name: 'src/behavior.py' }));
  fireEvent.change(await ide.findByRole('textbox', { name: 'Source code' }), { target: { value: 'factory_draft = True\n' } });
  act(() => useLocale.getState().setLocale('zh-CN'));
  expect(ide.getByText('实验性').title).toBe('可视化开发仍在开发中。');
  act(() => useLocale.getState().setLocale('en'));
  fireEvent.click(ide.getByRole('button', { name: 'Close Pack IDE' }));
  expect((screen.getByLabelText('卡包名称') as HTMLInputElement).value).toBe('Packaging draft');
  fireEvent.click(screen.getByRole('button', { name: 'Pack IDE Experimental' }));
  expect((screen.getByRole('textbox', { name: 'Source code' }) as HTMLTextAreaElement).value).toBe('factory_draft = True\n');
  screen.unmount();
  useNodeSurfaceStore.getState().setDraft(key, '');
});

it('preserves a dirty source buffer when another editor changes disk and pauses saves', async () => {
  const write = vi.spyOn(worldApi, 'writePackProjectFile');
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'src/behavior.py' }));
  const editor = await screen.findByRole('textbox', { name: 'Source code' });
  fireEvent.change(editor, { target: { value: 'my_draft = True\n' } });
  vi.mocked(worldApi.getPackProject).mockResolvedValue({ ...project, revision: 'b'.repeat(64), files: project.files.map(file => ({ ...file, sha256: 'b'.repeat(64) })) });
  fireEvent(window, new Event('focus'));
  await screen.findByText('Files changed externally');
  expect((editor as HTMLTextAreaElement).value).toBe('my_draft = True\n');
  expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  expect(write).not.toHaveBeenCalled();
});

it('discards the old conflict when returning to the project launcher', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'src/behavior.py' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Source code' }), { target: { value: 'draft = True\n' } });
  vi.mocked(worldApi.getPackProject).mockResolvedValue({ ...project, revision: 'b'.repeat(64) });
  fireEvent(window, new Event('focus'));
  await screen.findByText('Files changed externally');
  fireEvent.click(screen.getByRole('button', { name: 'Back to projects' }));
  expect(screen.queryByText('Files changed externally')).toBeNull();
  expect(screen.getByRole('button', { name: 'Create project' })).toBeTruthy();
});

it('opens real Python implementation by default and saves edits from closed tabs', async () => {
  const write = vi.spyOn(worldApi, 'writePackProjectFile').mockImplementation(async (_id, file) => ({ path: file.path, content: file.content, sha256: 'b'.repeat(64) }));
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Code' }));
  const editor = await screen.findByRole('textbox', { name: 'Source code' });
  expect((editor as HTMLTextAreaElement).value).toContain('handwritten = True');
  fireEvent.change(editor, { target: { value: 'changed = True\n' } });
  fireEvent.click(screen.getByRole('button', { name: 'Close src/behavior.py' }));
  fireEvent.click(screen.getByRole('button', { name: 'Design' }));
  fireEvent.click(screen.getByRole('button', { name: 'src/behavior.py' }));
  expect((screen.getByRole('textbox', { name: 'Source code' }) as HTMLTextAreaElement).value).toBe('changed = True\n');
  fireEvent.click(screen.getByRole('button', { name: 'Close src/behavior.py' }));
  fireEvent.keyDown(screen.getByRole('region', { name: 'Pack IDE' }), { key: 's', ctrlKey: true });
  await screen.findByText('Project files saved.');
  expect(write).toHaveBeenCalledWith('project', { path: 'src/behavior.py', content: 'changed = True\n', expected_sha256: hash });
});

it('opens failing diagnostics automatically and links the actual source', async () => {
  vi.mocked(worldApi.checkPackProject).mockResolvedValue({ ok: false, level: 'static', diagnostics: [{ severity: 'error', message: 'Python syntax error: expected colon', path: 'src/behavior.py', line: 3, column: 8 }] });
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Check' }));
  const diagnostic = await screen.findByRole('button', { name: /Python syntax error: expected colon/ });
  fireEvent.click(diagnostic);
  await screen.findByRole('textbox', { name: 'Source code' });
  expect(screen.getByRole('tab', { name: 'behavior.py' }).getAttribute('aria-selected')).toBe('true');
});

it('only offers the publishing handoff after a checked export and invalidates it on an edit', async () => {
  const build = vi.spyOn(worldApi, 'buildPackProject').mockResolvedValue(new Blob(['schema3'], { type: 'application/vnd.oaw.pack' }));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:pack'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = await opened();
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check' }));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Build / Export' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Build / Export' }));
  fireEvent.click(await screen.findByText('Publish this version'));
  await screen.findByRole('link', { name: 'Publish on Marketplace' });
  expect(build).toHaveBeenCalledOnce();
  expect(build).toHaveBeenCalledWith('project', { expected_revision: hash });
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText(/Output template/), { target: { value: 'Changed' } });
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
});
