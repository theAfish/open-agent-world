// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, expect, test, vi } from 'vitest';
import { PublishApplication } from './PublishApplication';
import { useLocale } from '../i18n';
import type { WorldCard } from '../types/world';
import { useWorldStore } from '../state/worldStore';

afterEach(() => { cleanup(); useWorldStore.setState({ cards: [] }); vi.unstubAllGlobals(); });

test('lists invalid workspace panes before allowing release creation', async () => {
  useLocale.setState({ locale: 'en' });
  const fetch = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/preview')
    ? { ready: false, panels: [], issues: [{ card_id: 'a', message: 'Page belongs to another Legion' }, { card_id: 'b', message: 'Terminal requires permission' }] }
    : [] }));
  vi.stubGlobal('fetch', fetch);
  const card = { id: 'team', name: 'Team', type: 'legion', config: {} } as WorldCard;
  render(<PublishApplication card={card} disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish application' }));
  await screen.findByText('Page belongs to another Legion');
  expect(screen.getByText('Terminal requires permission')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled();
  expect(fetch.mock.calls.filter(([url]) => url === '/api/deployments')).toHaveLength(1); // history only
});

test('publishes the saved Legion with explicit terminal permission and gives a quoted deployment command', async () => {
  useLocale.setState({ locale: 'en' });
  const release = { id: 'release-123', legion_id: 'team', name: 'Customer app', source_path: 'D:\\Projects\\Customer $data', created_at: '2026-09-20T00:00:00Z' };
  const fetch = vi.fn(async (url: string, options?: RequestInit) => ({ ok: true, json: async () => url.endsWith('/preview')
    ? { ready: true, panels: [{ card_id: 'chat', name: 'Conversation' }], issues: [], warnings: [{ message: 'Business access' }] }
    : options?.method === 'POST' ? release : [] }));
  vi.stubGlobal('fetch', fetch);
  const card: WorldCard = { id: 'team', name: 'Team', type: 'legion', position: { x: 0, y: 0 }, size: { width: 800, height: 550 }, expanded: false, status: 'available', config: { workspace_layout: { version: 2, root: { kind: 'pane', view: { card_id: 'chat' } } } } };
  render(<PublishApplication card={card} disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish application' }));
  fireEvent.change(screen.getByLabelText('Application name'), { target: { value: 'Customer app' } });
  fireEvent.click(screen.getByLabelText('Allow users to run Sandbox terminal commands'));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create release' })).toBeEnabled());
  expect(screen.getByRole('note')).toHaveTextContent('business operations and computation');
  fireEvent.click(screen.getByRole('button', { name: 'Create release' }));
  await screen.findByText('Deploy this release');
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/deployments', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ legion_ids: ['team'], name: 'Customer app', allow_terminal: true }),
  })));
  expect(screen.getByText(/python scripts\/deploy.py/)).toHaveTextContent("--source 'D:\\Projects\\Customer $data' --release release-123 --open");
  expect(screen.getByText(/close the engineering application/)).toBeVisible();
});

test('selects and orders workspaces, and prevents publishing an empty selection', async () => {
  useLocale.setState({ locale: 'en' });
  const card = { id: 'team', name: 'Team', type: 'legion', config: {} } as WorldCard;
  const other = { ...card, id: 'notes', name: 'Notes' };
  useWorldStore.setState({ cards: [card, other, { ...card, id: 'private', name: 'Private' }] });
  const fetch = vi.fn(async (url: string, options?: RequestInit) => ({ ok: true, json: async () => url.endsWith('/preview')
    ? { ready: true, panels: [], issues: [] }
    : options?.method === 'POST' ? { id: 'release', legion_id: 'notes', name: 'Team', source_path: '/data', created_at: '2026-10-29' } : [] }));
  vi.stubGlobal('fetch', fetch);
  render(<PublishApplication card={card} disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish application' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Notes' }));
  fireEvent.click(screen.getByRole('button', { name: 'Move Notes up' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create release' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Create release' }));
  await screen.findByText('Deploy this release');
  expect(fetch).toHaveBeenCalledWith('/api/deployments', expect.objectContaining({ body: JSON.stringify({ legion_ids: ['notes', 'team'], name: 'Team', allow_terminal: false }) }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Notes Default' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Team Default' }));
  expect(screen.getByText('Select at least one workspace.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled();
});
