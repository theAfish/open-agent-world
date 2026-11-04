// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { PackStore } from './PackStore';
import { StorePackPreview } from './StorePackPreview';
import { paintPackCover } from './pack3d/artwork';
import type { StoreDetail, StorePack, StorePage, StoreState, StoreVersion } from '../types/packs';

vi.mock('./pack3d/artwork', () => ({ paintPackCover: vi.fn() }));

const state: StoreState = { installed_version: null, loaded_version: null, available_version: '0.10.0', update_available: false, restart_required: false, can_install: true };
const pack: StorePack = { ...state, id: 'example.greeter', name: 'Greeter', summary: 'External Greeter Pack', description: 'A greeting card.', latest_version: '0.10.0' };
const detail: StoreDetail = { ...pack, versions: ['0.10.0'], versions_next_cursor: null };
const iconUrl = 'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg"><path d="M2 2h20v20H2z"/></svg>');
const artworkUrl = 'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="20" fill="red"/></svg>');
const version: StoreVersion = { ...state, pack_id: pack.id, version: '0.10.0', size_bytes: 10, sha256: '0'.repeat(64), manifest: {
  id: pack.id, version: '0.10.0', compatibility: { oaw: '>=0.1.0', plugin_api: '1.23', frontend_api: 1 },
  dependencies: { packs: [{ id: 'science.viewer', version: '>=1' }] }, runtime: { sandbox: { python: ['colorama==0.4.6'] } },
} };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('uses authored packaging and artwork in the catalog and the reviewed version preview in details', async () => {
  const preview = { packaging: 'premium' as const, accent_color: '#aa3366', count: 3, icon: 'bot', icon_url: iconUrl, artwork_url: artworkUrl };
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [{ ...pack, preview }], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue({ ...detail, preview });
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue({ ...version, preview: { ...preview, packaging: 'collector', icon_url: artworkUrl, artwork_url: iconUrl } });
  const install = vi.spyOn(worldApi, 'installStorePack');
  const view = render(<PackStore />);
  const open = await screen.findByRole('button', { name: 'View Greeter details' });
  expect(view.container.querySelector('.library-pack')?.getAttribute('data-packaging')).toBe('premium');
  expect(view.container.querySelector('.library-pack')?.getAttribute('style')).toContain('#aa3366');
  expect(view.container.querySelector('.library-pack')?.getAttribute('data-renderer')).toBe('fallback');
  await waitFor(() => expect(paintPackCover).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ icon: iconUrl, artwork: artworkUrl })));
  const copy = view.container.querySelector('.pack-catalog-copy')!;
  expect(copy.textContent).toBe(pack.name);
  expect(view.container.querySelector('.library-pack')?.getAttribute('data-pack-version')).toBe('0.10.0');
  expect(screen.getByText('Version 0.10.0')).toBeTruthy();
  fireEvent.click(open);
  await screen.findByText('colorama==0.4.6');
  expect(view.container.querySelector('.library-pack')?.getAttribute('data-packaging')).toBe('collector');
  expect(paintPackCover).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ icon: artworkUrl, artwork: iconUrl }));
  expect(install).not.toHaveBeenCalled();
});

it('browses remote Pack details in hosted read-only mode without offering installation', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue(detail);
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue(version);
  const install = vi.spyOn(worldApi, 'installStorePack');
  render(<PackStore readOnly />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  expect(await screen.findByText('colorama==0.4.6')).toBeTruthy();
  expect(screen.getByText('Pack installation is managed by your hosting administrator.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Get' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Update' })).toBeNull();
  expect(install).not.toHaveBeenCalled();
});

it.each([
  ['remote URL', 'https://third-party.example/image.png'],
  ['non-image data', 'data:text/html;base64,PHNjcmlwdD4='],
  ['oversized image', 'data:image/png;base64,' + 'A'.repeat(4 * Math.ceil((256 * 1024 + 1) / 3))],
])('keeps catalog icons without loading untrusted preview images: %s', (_name, value) => {
  const view = render(<StorePackPreview pack={{ ...pack, preview: { packaging: 'standard', icon: 'bot', icon_url: value, artwork_url: value } }} onClick={() => {}} />);
  expect(view.container.querySelector('.pack-icon-source .lucide-bot')).toBeTruthy();
  expect(view.container.querySelector('.catalog-asset-icon')).toBeNull();
  expect(paintPackCover).toHaveBeenLastCalledWith(expect.any(HTMLCanvasElement), expect.objectContaining({ artwork: undefined, icon: expect.stringContaining('data:image/svg+xml,') }));
});

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
  const installed = vi.fn();
  render(<PackStore onInstalled={installed} />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  expect(await screen.findByText('colorama==0.4.6')).toBeTruthy();
  expect(screen.getByText('science.viewer >=1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Get' }));
  expect(await screen.findByText('Installed · Restart required')).toBeTruthy();
  expect(install.mock.calls[0].slice(0, 2)).toEqual([pack.id, '0.10.0']);
  expect(installed).toHaveBeenCalledWith(expect.objectContaining({ restart_required: true }));
  expect((screen.getByRole('button', { name: 'Installed' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Back to Store' }));
  expect(screen.getByText('Restart required').closest('.library-pack')?.getAttribute('data-pack-notice')).toBe('restart');
});

it('uses backend update decisions and contains a failed install', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [{ ...pack, installed_version: '0.9.0', update_available: true }], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue({ ...detail, installed_version: '0.9.0', update_available: true });
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue({ ...version, installed_version: '0.9.0', update_available: true });
  const install = vi.spyOn(worldApi, 'installStorePack').mockRejectedValue(new Error('Pack requires missing dependency'));
  const installed = vi.fn();
  render(<PackStore onInstalled={installed} />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  expect(install).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Pack requires missing dependency');
  expect((screen.getByRole('button', { name: 'Update' }) as HTMLButtonElement).disabled).toBe(false);
  expect(installed).not.toHaveBeenCalled();
});

it('shows the same environment plan as local installs and blocks an unresolved dependency', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue(detail);
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue({ ...version, plan: {
    pack_id: pack.id, name: pack.name, version: version.version, source: 'store', restart_required: true,
    dependencies: [{ id: 'science.viewer', version: '>=1', state: 'missing' }],
    environments: [{ id: 'analysis', target: 'sandbox', provider: 'python', management: 'managed', state: 'on_use', python: ['ase'], node: [] }],
    warnings: [], blockers: ['Install science.viewer first'],
  } });
  const install = vi.spyOn(worldApi, 'installStorePack');
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  expect(await screen.findByRole('region', { name: 'Installation plan' })).toBeTruthy();
  expect(screen.getByText('Prepared when used')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('Install science.viewer first');
  expect((screen.getByRole('button', { name: 'Unavailable' }) as HTMLButtonElement).disabled).toBe(true);
  expect(within(screen.getByRole('complementary', { name: 'Install Pack' })).getByRole('alert').textContent).toContain('Install science.viewer first');
  expect(install).not.toHaveBeenCalled();
});

it('explains an incompatible latest version next to the action and reinstalls a compatible version', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue({ ...detail, versions: ['0.10.0', '0.9.0'] });
  const metadata = vi.spyOn(worldApi, 'getStoreVersion').mockImplementation(async (_id, requested) => requested === '0.9.0'
    ? { ...version, version: '0.9.0', available_version: '0.9.0' }
    : { ...version, can_install: false, plan: {
      pack_id: pack.id, name: pack.name, version: version.version, source: 'store', restart_required: true,
      dependencies: [], environments: [], warnings: [],
      blockers: ['Pack requires Plugin API 1.43; host provides 1.42. Upgrade OAW before installing.'],
    } });
  const install = vi.spyOn(worldApi, 'installStorePack').mockResolvedValue({ ...state, installed_version: '0.9.0', available_version: '0.9.0', restart_required: true, can_install: false });
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  const action = await screen.findByRole('button', { name: 'Unavailable' });
  const panel = screen.getByRole('complementary', { name: 'Install Pack' });
  expect(within(panel).getByRole('alert').textContent).toContain('Requires Plugin API 1.43; this OAW provides 1.42. Upgrade OAW first.');
  expect(action.getAttribute('aria-describedby')).toBe('store-install-reason');
  expect((action as HTMLButtonElement).disabled).toBe(true);
  expect(install).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Version'), { target: { value: '0.9.0' } });
  const get = await screen.findByRole('button', { name: 'Get' });
  expect((get as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(get);
  await screen.findByRole('button', { name: 'Installed' });
  expect(install.mock.calls[0].slice(0, 2)).toEqual([pack.id, '0.9.0']);
  expect(metadata.mock.calls.at(-1)?.slice(0, 2)).toEqual([pack.id, '0.9.0']);
});

it('ignores a late version response after the user chooses a different version', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue({ ...detail, versions: ['0.10.0', '0.9.0'] });
  let resolveLatest!: (value: StoreVersion) => void;
  const metadata = vi.spyOn(worldApi, 'getStoreVersion').mockImplementationOnce(() => new Promise(resolve => { resolveLatest = resolve; }))
    .mockResolvedValue({ ...version, version: '0.9.0', available_version: '0.9.0' });
  const install = vi.spyOn(worldApi, 'installStorePack').mockResolvedValue({ ...state, installed_version: '0.9.0', available_version: '0.9.0', can_install: false });
  render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  await vi.waitFor(() => expect(metadata).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByLabelText('Version'), { target: { value: '0.9.0' } });
  await screen.findByRole('button', { name: 'Get' });
  await act(async () => resolveLatest(version));
  fireEvent.click(screen.getByRole('button', { name: 'Get' }));
  await screen.findByRole('button', { name: 'Installed' });
  expect(metadata.mock.calls[0][2]?.aborted).toBe(true);
  expect(install.mock.calls[0].slice(0, 2)).toEqual([pack.id, '0.9.0']);
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

it('distinguishes acquisition from preparation and clears a stale pending hint after success', async () => {
  vi.spyOn(worldApi, 'getStorePacks').mockResolvedValue({ items: [pack], next_cursor: null });
  vi.spyOn(worldApi, 'getStorePack').mockResolvedValue(detail);
  vi.spyOn(worldApi, 'getStoreVersion').mockResolvedValue(version);
  let complete!: (value: StoreState) => void;
  vi.spyOn(worldApi, 'installStorePack').mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const view = render(<PackStore />);
  fireEvent.click(await screen.findByRole('button', { name: 'View Greeter details' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Get', exact: true }));
  expect((screen.getByRole('button', { name: 'Getting Pack' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('Getting the Pack (0.0 MB)…')).toBeTruthy();
  view.rerender(<PackStore installations={{ restart_required: false, versions: [], operations: [{ id: 'operation', digest: '0'.repeat(64),
    pack_id: pack.id, version: version.version, state: 'preparing', error: null, updated_at: 1 }] }} />);
  expect((screen.getByRole('button', { name: 'Installing' }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => complete({ ...state, installed_version: version.version, can_install: false, restart_required: true }));
  expect((screen.getByRole('button', { name: 'Installed' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('button', { name: 'Installing' })).toBeNull();
});
