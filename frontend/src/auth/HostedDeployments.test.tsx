// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { HostedDeployments } from './HostedDeployments';
import type { HostedSession } from './hostedSession';

const session: HostedSession = { hosted: true, authenticated: true, user: { id: 'admin', username: 'admin', display_name: 'Admin', role: 'admin' },
  instance: { id: 'engineering', application_id: 'studio', name: 'Engineering', mode: 'builder' }, login_methods: ['password'] };
const initial = { releases: [{ id: 'release-a', name: 'Research', created_at: '2026-10-20T00:00:00Z' }], release_error: null, jobs: [],
  users: [{ id: 'alice', username: 'alice', role: 'user', enabled: true, entry_id: 'old' }],
  profiles: [{ id: 'old', user_id: 'alice', name: 'Old canvas', mode: 'builder' }, { id: 'new', user_id: 'alice', name: 'Research', mode: 'runtime' }] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('creates a new independent deployment without silently switching a user, and explicitly selects an entry', async () => {
  let entry = 'old';
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') { entry = JSON.parse(String(init.body)).profile_id; return Response.json({}); }
    if (init?.method === 'POST') return Response.json({ job_ids: ['job'] }, { status: 202 });
    return Response.json({ ...initial, users: [{ ...initial.users[0], entry_id: entry }] });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<HostedDeployments session={session} />);
  expect(screen.getByRole('link', { name: 'Open engineering canvas' }).getAttribute('href')).toBe('/');
  await screen.findByLabelText('alice');
  fireEvent.change(screen.getByLabelText('Published version'), { target: { value: 'release-a' } });
  fireEvent.click(screen.getByLabelText('alice'));
  fireEvent.click(screen.getByRole('button', { name: 'Create private workspaces' }));
  await screen.findByText(/Deployment queued/);
  const submitted = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(submitted[1]?.body))).toEqual({ request_id: expect.any(String), release_id: 'release-a', user_ids: ['alice'], mode: 'runtime' });
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  fireEvent.change(screen.getByLabelText('alice workspace'), { target: { value: 'new' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set entry' }));
  await waitFor(() => expect(entry).toBe('new'));
  await screen.findByText(/entry has changed/);
});
