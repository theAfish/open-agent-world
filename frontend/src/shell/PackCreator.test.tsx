// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import type { CreatorInspection } from '../types/packs';
import type { LegionSummary } from '../types/world';
import { PackCreator } from './PackCreator';

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
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  await screen.findByRole('button', { name: 'Export .oawpack' });
  expect(inspect.mock.calls[0][0].include_state_nodes).toEqual([]);
  expect(inspect.mock.calls[0][0].id).toBe('local.legion1234abcd');
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
  const blob = new Blob(['pack']);
  const download = vi.spyOn(worldApi, 'exportContentPack').mockResolvedValue(blob);
  const createObjectURL = vi.fn(() => 'blob:pack');
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = render(<PackCreator legion={legion} />);
  fireEvent.click(screen.getByRole('button', { name: 'Make a Pack…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Export .oawpack' }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(createObjectURL).toHaveBeenCalledWith(blob);
  expect(download.mock.calls[0][0].include_state_nodes).toEqual([]);
});
