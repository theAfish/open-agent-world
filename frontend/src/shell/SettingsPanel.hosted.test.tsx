// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SettingsPanel } from './SettingsPanel';
import * as hostedAuth from '../auth/hostedSession';
import { useWorldStore } from '../state/worldStore';

beforeEach(() => {
  useWorldStore.setState({ settingsOpen: true });
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(true);
  vi.spyOn(hostedAuth, 'currentHostedSession').mockReturnValue({
    hosted: true, authenticated: true, user: { id: 'admin', username: 'admin', display_name: 'Admin', role: 'admin' },
    instance: null, login_methods: ['password'], shared_models: true,
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('saves and reopens value/secret settings through the real admin request without exposing a saved secret', async () => {
  let stored = { revision: 0, environment_variables: {} as Record<string, unknown>, secret_bindings: {} };
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/status')) return Response.json({ shared_environment_enabled: true });
    expect(url).toBe('/hosting/admin/environment');
    if (init?.method === 'PUT') {
      const body = JSON.parse(init.body as string);
      expect(body.revision).toBe(stored.revision);
      expect(body.workspace_root).toBeUndefined();
      expect(body.runtime).toBeUndefined();
      expect(body.secrets).toEqual(stored.revision === 0
        ? expect.objectContaining({ [Object.keys(body.secrets)[0]]: 'test-key' }) : {});
      stored = { revision: stored.revision + 1, environment_variables: body.environment_variables,
        secret_bindings: { ...stored.secret_bindings, ...Object.fromEntries(Object.keys(body.secrets).map(key => [key, true])) } };
    }
    return Response.json(stored);
  });
  vi.stubGlobal('fetch', request);
  const view = render(<SettingsPanel initialSection="sandbox" />);
  await screen.findByText('No variables.');
  fireEvent.click(screen.getByRole('button', { name: 'Add variable' }));
  fireEvent.change(screen.getByLabelText('Environment variable 1 name'), { target: { value: 'REGION' } });
  fireEvent.change(screen.getByLabelText('Environment variable 1 value'), { target: { value: 'hangzhou' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add variable' }));
  fireEvent.change(screen.getByLabelText('Environment variable 2 name'), { target: { value: 'API_TOKEN' } });
  fireEvent.change(screen.getByLabelText('Environment variable 2 type'), { target: { value: 'secret' } });
  fireEvent.change(screen.getByLabelText('Environment variable 2 value'), { target: { value: 'test-key' } });
  expect(screen.queryByRole('option', { name: 'Path' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await screen.findByText('Settings saved');
  expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('hangzhou');
  expect((screen.getByLabelText('Environment variable 2 value') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Close settings' }));
  await waitFor(() => expect(useWorldStore.getState().settingsOpen).toBe(false));
  view.unmount();
  useWorldStore.setState({ settingsOpen: true });
  render(<SettingsPanel initialSection="sandbox" />);
  await screen.findByText('Configured');
  expect((screen.getByLabelText('Environment variable 2 value') as HTMLInputElement).value).toBe('');
  expect(stored.environment_variables).toMatchObject({ REGION: 'hangzhou', API_TOKEN: { secret_ref: expect.any(String) } });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await screen.findByText('Settings saved');
  expect(stored.revision).toBe(2);
});

it('keeps the draft on conflict and allows explicit reload', async () => {
  const request = vi.fn(async (url: string, init?: RequestInit) => url.endsWith('/status')
    ? Response.json({ shared_environment_enabled: true }) : init?.method === 'PUT'
    ? Response.json({ error: { message: 'Settings changed. Reload before saving.' } }, { status: 409 })
    : Response.json({ revision: 1, environment_variables: { REGION: 'saved' }, secret_bindings: {} }));
  vi.stubGlobal('fetch', request);
  render(<SettingsPanel initialSection="sandbox" />);
  await screen.findByLabelText('Environment variable 1 value');
  fireEvent.change(screen.getByLabelText('Environment variable 1 value'), { target: { value: 'draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await screen.findByRole('alert');
  expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('draft');
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft and reload' }));
  await waitFor(() => expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('saved'));
});
