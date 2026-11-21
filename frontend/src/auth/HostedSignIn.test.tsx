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

it('toggles password plaintext visibility and completes two-step email-then-phone registration', async () => {
  const replace = vi.fn();
  vi.stubGlobal('location', { ...window.location, replace });
  const request = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/auth/code/send') return new Response(JSON.stringify({ sent: true, cooldown_seconds: 60 }), { status: 200 });
    if (url === '/auth/register/email') {
      return new Response(JSON.stringify({ register_ticket: 't'.repeat(43), email: 'zyc@example.com' }), { status: 200 });
    }
    if (url === '/auth/register/complete') {
      return new Response(JSON.stringify({ authenticated: true, merged: false, user: { id: 'u1', username: 'zyc', display_name: 'zyc' } }), { status: 200 });
    }
    return new Response('{}', { status: 400 });
  });
  vi.stubGlobal('fetch', request);

  render(<HostedSignIn session={{ hosted: true, authenticated: false, user: null, login_methods: ['password', 'code'], registration_enabled: true, instance: null }} />);

  // Default screen is Login; eye button toggles password input type
  const loginPwd = screen.getByLabelText('Password') as HTMLInputElement;
  expect(loginPwd.type).toBe('password');
  fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
  expect(loginPwd.type).toBe('text');
  expect((screen.getByLabelText('Keep me signed in for 30 days') as HTMLInputElement).checked).toBe(true);

  // Navigate to Register: Step 2 phone field is hidden until Step 1 email passes
  fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
  expect(screen.queryByLabelText('Phone number')).toBeNull();

  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'zyc_dev' } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'zyc@example.com' } });
  fireEvent.change(screen.getByLabelText('Email code'), { target: { value: '123456' } });
  fireEvent.change(screen.getByLabelText('Set password'), { target: { value: 'lowercase123' } });
  fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'lowercase123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByRole('alert').textContent).toBe('Password must be at least 8 characters and include uppercase, lowercase, and digits.');
  expect(request).not.toHaveBeenCalledWith('/auth/register/email', expect.anything());

  fireEvent.change(screen.getByLabelText('Set password'), { target: { value: 'Pass1234' } });
  fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'Wrong123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByRole('alert').textContent).toBe('Passwords do not match.');
  expect(request).not.toHaveBeenCalledWith('/auth/register/email', expect.anything());

  // Fix confirmation password -> advances to Step 2 (Bind phone)
  fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'Pass1234' } });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await waitFor(() => expect(screen.getByLabelText('Phone number')).toBeTruthy());

  fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '13800008801' } });
  fireEvent.change(screen.getByLabelText('SMS code'), { target: { value: '654321' } });
  fireEvent.click(screen.getByRole('button', { name: 'Complete registration' }));

  await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
  expect(request).toHaveBeenCalledWith('/auth/register/complete', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ register_ticket: 't'.repeat(43), phone: '13800008801', code: '654321', remember_me: true }),
  }));
});
