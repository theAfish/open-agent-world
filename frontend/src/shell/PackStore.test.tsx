// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { PackStore } from './PackStore';
import type { StoreDetail, StorePack, StorePage, StoreState, StoreVersion } from '../types/packs';

const state: StoreState = { installed_version: null, loaded_version: null, available_version: '0.10.0', update_available: false, restart_required: false, can_install: true };
const pack: StorePack = { ...state, id: 'example.greeter', name: 'Greeter', summary: 'External Greeter Pack', description: 'A greeting card.', latest_version: '0.10.0' };
const detail: StoreDetail = { ...pack, versions: ['0.10.0'], versions_next_cursor: null };
const version: StoreVersion = { ...state, pack_id: pack.id, version: '0.10.0', size_bytes: 10, sha256: '0'.repeat(64), manifest: {
  id: pack.id, version: '0.10.0', compatibility: { oaw: '>=0.1.0', plugin_api: '1.23', frontend_api: 1 },
  dependencies: { packs: [{ id: 'science.viewer', version: '>=1' }] }, runtime: { sandbox: { python: ['colorama==0.4.6'] } },
} };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('searches and paginates with the server cursor', async () => {
  const catalog = vi.spyOn(worldApi, 'getStorePacks').mockResolvedValueOnce({ items: [pack], next_cursor: pack.id })
    .mockResolvedValueOnce({ items: [{ ...pack, id: 'example.next', name: 'Next' }], next_cursor: null })
    .mockResolvedValue({ items: [], next_cursor: null });
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
  expect(await screen.findByRole('heading', { name: 'Next' })).toBeTruthy();
  expect(catalog.mock.calls[1].slice(0, 2)).toEqual(['', pack.id]);
  fireEvent.change(screen.getByLabelText('Search packs'), { target: { value: 'missing' } });
  expect(await screen.findByText('No matching packs')).toBeTruthy();
  expect(catalog.mock.calls.at(-1)?.slice(0, 2)).toEqual(['missing', undefined]);
});

it('displays requirements and installs an explicit version with restart state', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue(detail);
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue(version);
  const install = vi.spyOn(worldApi, 'installStorePack').mockResolvedValue({ ...state, installed_version: '0.10.0', restart_required: true, can_install: false });
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  expect(await screen.findByText('colorama==0.4.6')).toBeTruthy();
  expect(screen.getByText('science.viewer >=1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Get' }));
  expect(await screen.findByText('Installed · Restart required')).toBeTruthy();
  expect(install.mock.calls[0].slice(0, 2)).toEqual([pack.id, '0.10.0']);
  expect((screen.getByRole('button', { name: 'Installed' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Back to Store' }));
  expect(screen.getByText('Installed · Restart required')).toBeTruthy();
});

it('uses backend update decisions and contains a failed install', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [{ ...pack, installed_version: '0.9.0', update_available: true }], next_cursor: null });
  vi.spyOn(worldApi, 'installStorePack').mockRejectedValue(new Error('Pack requires missing dependency'));
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Pack requires missing dependency');
  expect((screen.getByRole('button', { name: 'Update' }) as HTMLButtonElement).disabled).toBe(false);
});

it('offers Retry after offline failure and recovers the catalog', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockRejectedValueOnce(new Error('Store is temporarily unavailable. Please retry.'))
    .mockResolvedValue({ items: [pack], next_cursor: null });
  render(<PackStore />);
  expect(await screen.findByText('Store unavailable')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('heading', { name: 'Greeter' })).toBeTruthy();
});

it('discards late search responses and aborts requests when leaving the Store', async () => {
  let resolveOld!: (value: StorePage) => void;
  const catalog = vi.spyOn(worldApi, 'getStorePacks').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
    .mockResolvedValue({ items: [{ ...pack, name: 'New' }], next_cursor: null });
  const view = render(<PackStore />);
  await vi.waitFor(() => expect(catalog).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByLabelText('Search packs'), { target: { value: 'New' } });
  expect(await screen.findByRole('heading', { name: 'New' })).toBeTruthy();
  await act(async () => resolveOld({ items: [pack], next_cursor: null }));
  expect(screen.queryByRole('heading', { name: 'Greeter' })).toBeNull();
  expect(catalog.mock.calls[0][2]?.aborted).toBe(true);
  view.unmount();
  expect(catalog.mock.calls[1][2]?.aborted).toBe(true);
});
