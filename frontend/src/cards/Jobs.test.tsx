// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { JobsBoard } from './Jobs';
import { useLocale } from '../i18n';
import type { BackgroundJob, JobsHost, JobsSnapshot } from './jobTypes';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const job = (id: string, extra: Partial<BackgroundJob> = {}): BackgroundJob => ({
  id, request_key: `Compute ${id}`, external_id: `external-${id}`, provider: 'test.compute', status: 'running', detail: 'Processing data',
  paused: false, wake_requested: false, revision: 2, created_at: 100, updated_at: 120, last_observed_at: null, next_check_at: null,
  observation_error: null, tracking_released: false, agent_id: 'agent', agent_name: 'Researcher', sandbox_id: 'sandbox',
  sandbox_name: 'Compute files', source_run_id: 'run', actions: ['refresh', 'pause', 'cancel'], ...extra,
});
function host(jobs = [job('one')]): JobsHost {
  useLocale.getState().setLocale('en');
  return { list: vi.fn().mockResolvedValue({ sources: [{ id: 'agent', name: 'Researcher', type: 'agent', can_manage: true }], jobs }),
    control: vi.fn().mockImplementation(async item => item) };
}

it('filters real provider observations and expands complete identities without fabricated progress', async () => {
  const api = host([job('one'), job('two', { status: 'unknown', provider: 'other.compute', actions: [] })]);
  render(<JobsBoard api={api} />);
  const running = await screen.findByRole('article', { name: 'Compute one' });
  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(within(running).getByText('Running')).toBeTruthy();
  expect(within(running).queryByText('Processing data')).toBeNull();
  expect(within(running).queryByText('Researcher')).toBeNull();
  expect(within(running).queryByText('external-one')).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Search jobs' })).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter jobs' }), { target: { value: 'attention' } });
  expect(screen.queryByRole('article', { name: 'Compute one' })).toBeNull();
  const tile = screen.getByRole('article', { name: 'Compute two' });
  expect(within(tile).queryByRole('button', { name: 'Cancel external job' })).toBeNull();
  fireEvent.click(within(tile).getByRole('button', { name: 'Show job details' }));
  expect(within(tile).getByText('Not observed yet')).toBeTruthy();
  expect(within(tile).getByText('external-two')).toBeTruthy();
  expect(within(tile).getByText('Researcher')).toBeTruthy();
  expect(within(tile).getByText('Processing data')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Search jobs' }));
  expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Search jobs' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Search jobs' }), { target: { value: 'absent' } });
  expect(screen.getByText('No matching jobs')).toBeTruthy();
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search jobs' }), { key: 'Escape' });
  expect(screen.queryByRole('textbox', { name: 'Search jobs' })).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Search jobs' }));
  expect(screen.getByRole('article', { name: 'Compute two' })).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter jobs' }), { target: { value: 'all' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Job provider' }), { target: { value: 'test.compute' } });
  expect(screen.getByRole('article', { name: 'Compute one' })).toBeTruthy();
  expect(screen.queryByRole('article', { name: 'Compute two' })).toBeNull();
});

it('separates monitoring from explicit cancellation and sends the observed revision', async () => {
  const api = host();
  render(<JobsBoard api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Show job details' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Pause monitoring' }));
  await waitFor(() => expect(api.control).toHaveBeenCalledWith(expect.objectContaining({ id: 'one', revision: 2 }), 'pause'));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Cancel external job' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel external job' }));
  expect(api.control).toHaveBeenCalledTimes(1);
  const confirmation = screen.getByRole('group', { name: 'Cancel external job' });
  fireEvent.click(within(confirmation).getByRole('button', { name: 'Cancel external job' }));
  await waitFor(() => expect(api.control).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'one' }), 'cancel'));
  expect(screen.getByText('Running')).toBeTruthy();
});

it('discards late reads after session changes and clears inaccessible cached work', async () => {
  let resolve!: (value: JobsSnapshot) => void;
  const old = host();
  old.list = vi.fn(() => new Promise<JobsSnapshot>(done => { resolve = done; }));
  const view = render(<JobsBoard api={old} />);
  const next = host([job('new')]);
  view.rerender(<JobsBoard api={next} />);
  await screen.findByRole('article', { name: 'Compute new' });
  await act(async () => resolve({ sources: [], jobs: [job('old')] }));
  expect(screen.queryByText('Compute old')).toBeNull();
  vi.mocked(next.list).mockRejectedValue(new Error('Access revoked'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh jobs' }));
  await screen.findByRole('alert');
  expect(screen.queryByText('Compute new')).toBeNull();
});

it('distinguishes an unconnected board from an empty session', async () => {
  const api = host([]);
  vi.mocked(api.list).mockResolvedValueOnce({ sources: [], jobs: [] });
  render(<JobsBoard api={api} />);
  await screen.findByText('Connect an Agent');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh jobs' }));
  await screen.findByText('No jobs in this session');
});
