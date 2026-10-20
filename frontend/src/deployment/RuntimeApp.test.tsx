// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as hostedAuth from '../auth/hostedSession';
import { deploymentRequest } from './api';
import { RuntimeApp } from './RuntimeApp';
import { configureWorkspaceApi } from '../api/client';
import { TEST_CATALOG } from '../state/catalog.fixture';

vi.mock('./api', () => ({ deploymentRequest: vi.fn() }));
vi.mock('../legions/LegionWorkspace', () => ({ WorkspaceWindow: ({ actions }: { actions: React.ReactNode }) => <section aria-label="Published workspace">{actions}</section> }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); configureWorkspaceApi(false); });

const session: hostedAuth.HostedSession = { hosted: true, authenticated: true, login_methods: ['password'],
  user: { id: 'alice', username: 'alice', display_name: 'Alice' },
  instance: { id: 'alice-profile', application_id: 'research', name: 'Research', mode: 'runtime' },
};

it('opens a hosted published workspace directly with the shared account controls', async () => {
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(true);
  vi.spyOn(hostedAuth, 'currentHostedSession').mockReturnValue(session);
  vi.mocked(deploymentRequest).mockResolvedValue({ id: 'published', name: 'Research', cards: [], legion: { id: 'workspace', type: 'legion', title: 'Research' }, catalog: TEST_CATALOG, permissions: {} });
  render(<RuntimeApp name="Research" />);
  await screen.findByRole('region', { name: 'Published workspace' });
  expect(screen.getByText('Alice')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy();
  expect(screen.queryByLabelText('Access password')).toBeNull();
  expect(deploymentRequest).not.toHaveBeenCalledWith('/deployment/session', expect.anything());
});

it('shows recovery controls, not shared-password login, when a hosted workspace cannot load', async () => {
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(true);
  vi.mocked(deploymentRequest).mockRejectedValue(new Error('unavailable'));
  render(<RuntimeApp name="Research" />);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  expect(screen.queryByLabelText('Access password')).toBeNull();
});

it('retains the shared-password login for existing standalone deployments', async () => {
  vi.spyOn(hostedAuth, 'isHosted').mockReturnValue(false);
  vi.mocked(deploymentRequest).mockRejectedValue(new Error('unauthenticated'));
  render(<RuntimeApp name="Research" />);
  expect(await screen.findByLabelText('Access password')).toBeTruthy();
});
