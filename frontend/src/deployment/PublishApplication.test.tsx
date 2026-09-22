// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, expect, test, vi } from 'vitest';
import { PublishApplication } from './PublishApplication';
import { useLocale } from '../i18n';
import type { WorldCard } from '../types/world';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('publishes the saved Legion with explicit terminal permission and gives a quoted deployment command', async () => {
  useLocale.setState({ locale: 'en' });
  const release = { id: 'release-123', legion_id: 'team', name: 'Customer app', source_path: 'D:\\Projects\\Customer $data', created_at: '2026-09-20T00:00:00Z' };
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => ({ ok: true, json: async () => options?.method === 'POST' ? release : [] }));
  vi.stubGlobal('fetch', fetch);
  const card: WorldCard = { id: 'team', name: 'Team', type: 'legion', position: { x: 0, y: 0 }, size: { width: 800, height: 550 }, expanded: false, status: 'available', config: { workspace_layout: { version: 2, root: { kind: 'pane', view: { card_id: 'chat' } } } } };
  render(<PublishApplication card={card} disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish application' }));
  fireEvent.change(screen.getByLabelText('Application name'), { target: { value: 'Customer app' } });
  fireEvent.click(screen.getByLabelText('Allow users to run Sandbox terminal commands'));
  fireEvent.click(screen.getByRole('button', { name: 'Create release' }));
  await screen.findByText('Deploy this release');
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/deployments', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ legion_id: 'team', name: 'Customer app', allow_terminal: true }),
  })));
  expect(screen.getByText(/python scripts\/deploy.py/)).toHaveTextContent("--source 'D:\\Projects\\Customer $data' --release release-123 --open");
  expect(screen.getByText(/close the engineering application/)).toBeVisible();
});
