// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AccountButton } from './AccountButton';
import { initializeHostedSession } from './hostedSession';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('opens account settings and admin user management while protecting the current admin row', async () => {
  const adminUser = {
    id: '11111111-1111-4111-8111-111111111111',
    username: 'zyc',
    display_name: 'ZYC',
    role: 'admin' as const,
    email: 'zyc@example.com',
    phone_masked: '+86 138****8801',
    has_password: true,
    session_persistent: true,
    session_expires_at: Math.floor(Date.now() / 1000) + 30 * 86400,
    active_sessions: 2,
  };
  const bobUser = {
    id: '22222222-2222-4222-8222-222222222222',
    username: 'bob',
    display_name: 'Bob',
    role: 'user' as const,
    email: 'bob@example.com',
    phone_masked: '+86 139****1234',
    enabled: true,
    created_at: 1000,
    last_login_at: 2000,
    instance: { id: '33333333-3333-4333-8333-333333333333', application_id: 'app', name: 'Bob App', mode: 'runtime' as const },
    provisioning: false,
  };

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/auth/me')) {
      return new Response(JSON.stringify({
        hosted: true,
        authenticated: true,
        user: adminUser,
        login_methods: ['password', 'code'],
        registration_enabled: true,
        instance: { id: '44444444-4444-4444-8444-444444444444', application_id: 'studio', name: 'Studio', mode: 'builder' },
      }), { status: 200 });
    }
    if (url.endsWith('/auth/account') && (!init?.method || init.method === 'GET')) {
      return new Response(JSON.stringify({ user: adminUser }), { status: 200 });
    }
    if (url.endsWith('/auth/account') && init?.method === 'PATCH') {
      const body = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ user: { ...adminUser, display_name: body.display_name } }), { status: 200 });
    }
    if (url.includes('/hosting/admin/users/')) {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ user: { ...bobUser, ...body } }), { status: 200 });
    }
    if (url.includes('/hosting/admin/users')) {
      return new Response(JSON.stringify({ users: [{ ...adminUser, enabled: true, created_at: 900, last_login_at: 2100, instance: null, provisioning: false }, bobUser] }), { status: 200 });
    }
    return new Response('{}', { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);

  await initializeHostedSession();
  render(<AccountButton />);

  // Update display name in Account Settings and verify top-bar button updates live
  fireEvent.click(screen.getByRole('button', { name: 'Account settings' }));
  await waitFor(() => expect(screen.getByText('+86 138****8801')).toBeTruthy());
  const nameInput = screen.getByDisplayValue('ZYC');
  fireEvent.change(nameInput, { target: { value: 'ZYC Updated' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(screen.getByText('ZYC Updated')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));

  // Open Admin User Management: internal UUIDs are not rendered, self row is protected, Bob row can be disabled
  fireEvent.click(screen.getByRole('button', { name: 'User management' }));
  await waitFor(() => expect(screen.getByText('bob@example.com')).toBeTruthy());
  expect(screen.queryByText('11111111')).toBeNull();
  expect(screen.queryByText('22222222')).toBeNull();
  const buttons = screen.getAllByRole('button', { name: 'Disable' }) as HTMLButtonElement[];
  expect(buttons[0].disabled).toBe(true);
  expect(buttons[1].disabled).toBe(false);
  fireEvent.click(buttons[1]);
  await waitFor(() => expect(screen.getByText('Account disabled and active sessions revoked.')).toBeTruthy());
});
