// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { HostedEnvironment } from './HostedEnvironment';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('saves and reopens value/secret settings through the real admin request without exposing a saved secret', async () => {
  let stored = { revision: 0, environment_variables: {} as Record<string, unknown>, secret_bindings: {} };
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    expect(url).toBe('/hosting/admin/environment');
    if (init?.method === 'PUT') {
      const body = JSON.parse(init.body as string);
      expect(body.revision).toBe(stored.revision);
      expect(body.secrets).toEqual(expect.objectContaining({ [Object.keys(body.secrets)[0]]: 'test-key' }));
      stored = { revision: stored.revision + 1, environment_variables: body.environment_variables,
        secret_bindings: Object.fromEntries(Object.keys(body.secrets).map(key => [key, true])) };
    }
    return Response.json(stored);
  });
  vi.stubGlobal('fetch', request);
  const view = render(<HostedEnvironment enabled />);
  await screen.findByText('No variables.');
  fireEvent.click(screen.getByRole('button', { name: 'Add variable' }));
  fireEvent.change(screen.getByLabelText('Environment variable 1 name'), { target: { value: 'REGION' } });
  fireEvent.change(screen.getByLabelText('Environment variable 1 value'), { target: { value: 'hangzhou' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add variable' }));
  fireEvent.change(screen.getByLabelText('Environment variable 2 name'), { target: { value: 'API_TOKEN' } });
  fireEvent.change(screen.getByLabelText('Environment variable 2 type'), { target: { value: 'secret' } });
  fireEvent.change(screen.getByLabelText('Environment variable 2 value'), { target: { value: 'test-key' } });
  expect(screen.queryByRole('option', { name: 'Path' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save environment variables' }));
  await screen.findByText('Environment settings saved.');
  expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('hangzhou');
  expect((screen.getByLabelText('Environment variable 2 value') as HTMLInputElement).value).toBe('');
  view.unmount();
  render(<HostedEnvironment enabled />);
  await screen.findByText('Configured');
  expect((screen.getByLabelText('Environment variable 2 value') as HTMLInputElement).value).toBe('');
  expect(stored.environment_variables).toMatchObject({ REGION: 'hangzhou', API_TOKEN: { secret_ref: expect.any(String) } });
});

it('keeps the draft on conflict and allows explicit reload', async () => {
  const request = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'PUT'
    ? Response.json({ error: { message: 'Settings changed. Reload before saving.' } }, { status: 409 })
    : Response.json({ revision: 1, environment_variables: { REGION: 'saved' }, secret_bindings: {} }));
  vi.stubGlobal('fetch', request);
  render(<HostedEnvironment enabled />);
  await screen.findByLabelText('Environment variable 1 value');
  fireEvent.change(screen.getByLabelText('Environment variable 1 value'), { target: { value: 'draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save environment variables' }));
  await screen.findByRole('alert');
  expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('draft');
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft and reload' }));
  await waitFor(() => expect((screen.getByLabelText('Environment variable 1 value') as HTMLInputElement).value).toBe('saved'));
});
