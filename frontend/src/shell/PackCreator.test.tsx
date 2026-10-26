// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import type { CreatorInspection } from '../types/packs';
import type { LegionSummary } from '../types/world';
import { PackCreator } from './PackCreator';
import { MARKETPLACE_PUBLISH_URL } from './PackPublishing';

const legion: LegionSummary = { id: '1234-abcd', name: 'Research team', description: 'A useful formation',
  node_count: 2, edge_count: 1, bounds: { width: 500, height: 300 }, node_types: ['agent', 'text'],
  plugin_ids: [], compatible: true, issues: [], revision: 1 };
const result: CreatorInspection = { can_export: true, issues: [],
  nodes: [{ key: 'note', name: 'My document', type: 'text', has_state: true, included: false }],
  manifest: { id: 'local.legion1234abcd', name: 'Research team', version: '0.1.0',
    dependencies: { packs: [{ id: 'core.pack', version: '==0.1.0' }] } } };

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('excludes initial content by default and requires a new check after changing the selection', async () => {
  const inspect = vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue(result);
  const screen = render(<PackCreator legion={legion} />);
  fireEvent.click(screen.getByRole('button', { name: 'Make a Pack…' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Pack packaging' }), { target: { value: 'paper' } });
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  await screen.findByRole('button', { name: 'Export .oawpack' });
  expect(inspect.mock.calls[0][0].include_state_nodes).toEqual([]);
  expect(inspect.mock.calls[0][0].id).toBe('local.legion1234abcd');
  expect(inspect.mock.calls[0][0].creator.packaging).toBe('paper');
  fireEvent.click(screen.getByRole('checkbox', { name: 'My document' }));
  expect(screen.queryByRole('button', { name: 'Export .oawpack' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  await screen.findByRole('button', { name: 'Export .oawpack' });
  expect(inspect.mock.calls[1][0].include_state_nodes).toEqual(['note']);
});

it('blocks export on publication errors and displays the actionable finding', async () => {
  vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue({ ...result, can_export: false,
    issues: [{ severity: 'error', path: 'Agent.config.api_key', message: 'Remove this credential or private binding before sharing.' }] });
  const screen = render(<PackCreator legion={legion} />);
  fireEvent.click(screen.getByRole('button', { name: 'Make a Pack…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  const exportButton = await screen.findByRole('button', { name: 'Export .oawpack' });
  expect((exportButton as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('Agent.config.api_key')).toBeTruthy();
});

it('downloads the artifact after a successful review', async () => {
  vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue(result);
  const blob = new Blob(['pack'], { type: 'application/vnd.oaw.pack' });
  const download = vi.spyOn(worldApi, 'exportContentPack').mockResolvedValue(blob);
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:pack');
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = render(<PackCreator legion={legion} />);
  fireEvent.click(screen.getByRole('button', { name: 'Make a Pack…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(createObjectURL.mock.calls[0][0]).toMatchObject({ size: blob.size, type: blob.type, name: 'local.legion1234abcd-0.1.0.oawpack' });
  expect(download.mock.calls[0][0].include_state_nodes).toEqual([]);
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.getByText(/replace its local\.\* ID/)).toBeTruthy();
});

function publisher() {
  const screen = render(<PackCreator legion={legion} />);
  fireEvent.click(screen.getByRole('button', { name: 'Make a Pack…' }));
  fireEvent.change(screen.getByLabelText('Pack ID'), { target: { value: 'my-team.my-pack' } });
  return screen;
}

it('requires a current checked export before handing off a hyphenated distribution ID', async () => {
  const inspect = vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue({ ...result, manifest: { ...result.manifest, id: 'my-team.my-pack' } });
  const download = vi.spyOn(worldApi, 'exportContentPack').mockResolvedValue(new Blob(['pack'], { type: 'application/vnd.oaw.pack' }));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:pack'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = publisher();
  expect(screen.getByText(/Author names do not grant permission/)).toBeTruthy();
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  const link = await screen.findByRole('link', { name: 'Publish on Marketplace' });
  expect(link.getAttribute('href')).toBe(MARKETPLACE_PUBLISH_URL);
  expect(link.getAttribute('target')).toBe('_blank');
  expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  expect(screen.getByText('my-team.my-pack-0.1.0.oawpack')).toBeTruthy();
  expect(inspect.mock.calls[0][0].id).toBe('my-team.my-pack');
  expect(download).toHaveBeenCalledOnce();
  // No claims that an unavailable browser opened or that the artifact was uploaded.
  expect(screen.getByText(/If the page does not open/)).toBeTruthy();
  expect(screen.getByText(/Submission enters review/)).toBeTruthy();
  screen.rerender(<PackCreator legion={{ ...legion, revision: 2 }} />);
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Export .oawpack' })).toBeNull();
});

it.each(['failed', 'empty', 'wrong-type', 'missing', 'download-unavailable'])('does not offer publishing after a %s export', async failure => {
  vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue({ ...result, manifest: { ...result.manifest, id: 'my-team.my-pack' } });
  const download = vi.spyOn(worldApi, 'exportContentPack');
  if (failure === 'failed') download.mockRejectedValue(new Error('Source is missing'));
  else download.mockResolvedValue(failure === 'missing' ? undefined as unknown as Blob : new Blob(failure === 'empty' ? [] : ['pack'],
    { type: failure === 'wrong-type' ? 'text/html' : 'application/vnd.oaw.pack' }));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => { throw new Error('Download unavailable'); }), revokeObjectURL: vi.fn() });
  const screen = publisher();
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.queryByRole('status')).toBeNull();
});

it('invalidates a prior export when editing or when a later check fails', async () => {
  const inspect = vi.spyOn(worldApi, 'inspectContentPack').mockResolvedValue({ ...result, manifest: { ...result.manifest, id: 'my-team.my-pack' } });
  vi.spyOn(worldApi, 'exportContentPack').mockResolvedValue(new Blob(['pack'], { type: 'application/vnd.oaw.pack' }));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:pack'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = publisher();
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  await screen.findByRole('link', { name: 'Publish on Marketplace' });
  fireEvent.change(screen.getByLabelText('Version'), { target: { value: '0.2.0' } });
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  await screen.findByRole('link', { name: 'Publish on Marketplace' });
  inspect.mockRejectedValue(new Error('Saved Legion missing'));
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Export .oawpack' })).toBeNull();
});
