// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HostedAdmin } from './HostedAdmin';
import type { HostedSession } from './hostedSession';
import * as hostedAuth from './hostedSession';
import { useWorldStore } from '../state/worldStore';

const session: HostedSession = { hosted: true, authenticated: true, user: { id: 'admin', username: 'admin', display_name: 'Admin', role: 'admin' }, instance: null, login_methods: ['password'], shared_models: true };
const catalog = { revision: 1, default_model: 'oaw:model:chat', connections: [{ id: 'shared', name: 'Company AI', adapter: 'openai', base_url: 'https://models.example/v1', enabled: true, auth_mode: 'api_key', api_key_configured: true, models: [{ id: 'chat', name: 'Assistant', model_id: 'test-chat', enabled: true }] }] };
beforeEach(() => {
  useWorldStore.setState({ settingsOpen: false });
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(true);
  vi.spyOn(hostedAuth, 'currentHostedSession').mockReturnValue(session);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('edits shared settings without a workspace, keeps saved keys hidden and routes discovery to administration', async () => {
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/deployments')) return Response.json({ releases: [], users: [], profiles: [], jobs: [] });
    if (url.endsWith('/status')) return Response.json({ shared_models_enabled: true, workspace_count: 3, users: [] });
    if (url.endsWith('/discover')) return Response.json({ models: [{ id: 'another', name: 'Another model' }], truncated: false });
    return Response.json({ ...catalog, revision: init?.method === 'PUT' ? 2 : 1 });
  });
  vi.stubGlobal('fetch', request);
  render(<HostedAdmin session={session} />);
  fireEvent.click(screen.getByRole('button', { name: 'Shared models' }));
  const key = await screen.findByLabelText('API key');
  expect((key as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Get available models' }));
  await screen.findByRole('option', { name: /Another model/ });
  expect(request).toHaveBeenCalledWith('/hosting/admin/models/discover', expect.objectContaining({ method: 'POST' }));
  fireEvent.change(key, { target: { value: 'test-only-new-key' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save shared models' }));
  await screen.findByText('Shared settings saved. Users can use the enabled models.');
  expect((screen.getByLabelText('API key') as HTMLInputElement).value).toBe('');
  expect(request).toHaveBeenCalledWith('/hosting/admin/models', expect.objectContaining({ method: 'PUT' }));
  expect(request.mock.calls.some(([url]) => url.startsWith('/api/'))).toBe(false);
});

it('does not load the administrator catalog for an ordinary user', async () => {
  const request = vi.fn(); vi.stubGlobal('fetch', request);
  render(<HostedAdmin session={{ ...session, user: { ...session.user!, role: 'user' } }} />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Administrator access required.'));
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Save shared models' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
});

it('opens the existing Settings dialog from the administration navigation', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/environment')) return Response.json({ revision: 0, environment_variables: {}, secret_bindings: {} });
    if (url.endsWith('/deployments')) return Response.json({ releases: [], users: [], profiles: [], jobs: [] });
    if (url.endsWith('/status')) return Response.json({ shared_models_enabled: true, shared_environment_enabled: true, workspace_count: 3, users: [] });
    return Response.json(catalog);
  }));
  render(<HostedAdmin session={session} />);
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  await screen.findByRole('button', { name: 'Add variable' });
  expect(screen.getByRole('dialog', { name: 'Settings' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeTruthy();
  expect(screen.queryByText(/delivery has not been enabled/)).toBeNull();
});
