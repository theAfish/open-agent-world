// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { HostedSignIn } from './HostedSignIn';
import type { HostedSession } from './hostedSession';

const session: HostedSession = { hosted: true, authenticated: false, user: null, login_methods: ['password', 'oidc'], instance: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('offers password and organization login without loading a workspace or registration', () => {
  render(<HostedSignIn session={session} />);
  expect(screen.getByLabelText('Username').getAttribute('autocomplete')).toBe('username');
  expect(screen.getByLabelText('Password').getAttribute('type')).toBe('password');
  expect(screen.getByRole('link', { name: 'Sign in with your organization' }).getAttribute('href')).toBe('/auth/oidc/login');
  expect(screen.queryByText('Register')).toBeNull();
});

it('posts credentials to the same-origin auth endpoint, clears passwords and gives a generic failure', async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'User does not exist' }), { status: 401 }));
  vi.stubGlobal('fetch', request);
  render(<HostedSignIn session={session} />);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' alice ' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'test-only-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Unable to sign in. Check your username and password.'));
  expect(request).toHaveBeenCalledWith('/auth/login', expect.objectContaining({ method: 'POST', body: JSON.stringify({ username: 'alice', password: 'test-only-password' }) }));
  expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
  expect(screen.queryByText('User does not exist')).toBeNull();
});

it('shows no-assignment guidance rather than a second login for an authenticated account', () => {
  render(<HostedSignIn session={{ ...session, authenticated: true, user: { id: 'alice', username: 'alice', display_name: 'Alice' } }} />);
  expect(screen.getByText('Your account has no workspace assigned. Contact your administrator.')).toBeTruthy();
  expect(screen.queryByLabelText('Password')).toBeNull();
  expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy();
});
