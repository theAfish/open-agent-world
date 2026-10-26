// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import type { PluginViewProps } from '../plugins/sdk';
import { Packer } from './FactoryViews';

const inspection = { can_export: true, entries: [], issues: [], manifest: { id: 'my-team.my-cards', version: '0.1.0' } };
const props = { card: { id: 'packer', config: { items: [{ kind: 'node', id: 'card', name: 'Card' }], params: {}, include_content: false } },
  level: 'workspace', host: { updateConfig: vi.fn().mockResolvedValue(undefined) } } as unknown as PluginViewProps;

beforeEach(() => {
  vi.spyOn(worldApi, 'getLegions').mockResolvedValue([]);
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:pack'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function factory({ invalid = false, empty = false } = {}) {
  return vi.spyOn(worldApi, 'factory').mockImplementation(async (_id, action) => {
    if (action === 'context') return { issues: [], inputs: { pack: { id: 'design', name: 'Design', config: { id: inspection.manifest.id } } } };
    if (action === 'inspect') return { ...inspection, can_export: !invalid };
    if (action === 'export') return new Blob(empty ? [] : ['pack'], { type: 'application/vnd.oaw.pack' });
    throw new Error(`Unexpected action: ${action}`);
  });
}

it('uses the existing inspection and export, and invalidates the handoff when options change', async () => {
  const api = factory();
  const screen = render(<Packer {...props} />);
  await screen.findByText('● Design');
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '导出 .oawpack' }));
  await screen.findByRole('link', { name: 'Publish on Marketplace' });
  expect(api.mock.calls.map(([, action]) => action)).toEqual(['context', 'inspect', 'export']);
  expect(screen.getByText('my-team.my-cards-0.1.0.oawpack')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.queryByRole('button', { name: '再次下载' })).toBeNull();
});

it.each([{ invalid: true }, { empty: true }])('blocks the handoff for an invalid inspection or empty export: %j', async options => {
  const api = factory(options);
  const screen = render(<Packer {...props} />);
  await screen.findByText('● Design');
  fireEvent.click(screen.getByRole('button', { name: '导出 .oawpack' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  expect(screen.queryByRole('button', { name: '再次下载' })).toBeNull();
  if (options.invalid) expect(api.mock.calls.map(([, action]) => action)).toEqual(['context', 'inspect']);
});

it('keeps publishing unavailable if the download mechanism throws', async () => {
  factory();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => { throw new Error('Download unavailable'); });
  const screen = render(<Packer {...props} />);
  await screen.findByText('● Design');
  fireEvent.click(screen.getByRole('button', { name: '导出 .oawpack' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Download unavailable'));
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
});
