// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ConversationRunHistory } from './ConversationRunHistory';
import { worldApi } from '../api/client';
import { useLocale } from '../i18n';
import type { RunActivityItem } from '../state/runActivity';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const run = { run_id: 'history', agent_id: 'agent', status: 'succeeded', tool_count: 55, tool_trace: [] };
function open(node: HTMLDetailsElement) { act(() => { node.open = true; fireEvent(node, new Event('toggle')); }); }

it('loads one directory page per click and only the selected call body, including after remount', async () => {
  useLocale.setState({ locale: 'en' });
  const rows: RunActivityItem[] = Array.from({ length: 55 }, (_, i) => ({
    id: `tool-${i}`, activity_id: `tool-${i}`, call_id: String(i), type: 'tool_completed', name: `read_${i}`,
  }));
  const listing = vi.spyOn(worldApi, 'getRunActivity').mockImplementation(async (_run, offset = 0) => ({
    items: rows.slice(offset, offset + 50), total: rows.length, next_offset: offset === 0 ? 50 : null,
  }));
  const body = vi.spyOn(worldApi, 'getRunActivityDetail').mockResolvedValue({
    id: 'tool-0', run_id: 'history', role: 'agent', run_attempt: 0, state: 'returned', started_at: '', updated_at: '',
    arguments: { path: 'selected-file' }, response: { stdout: 'selected-result' },
  });
  const view = render(<ConversationRunHistory run={run} />);
  expect(view.container.querySelector('summary')?.textContent).toBe('Work log');
  expect(listing).not.toHaveBeenCalled();
  open(view.container.querySelector('.conversation-history')!);
  await screen.findByText('read_49');
  expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull();
  expect(listing).toHaveBeenCalledTimes(1);
  expect(screen.queryByText('read_50')).toBeNull();
  expect(body).not.toHaveBeenCalled();
  open(view.container.querySelector('.run-tool')!);
  await screen.findByText('selected-result');
  expect(body).toHaveBeenCalledTimes(1);
  expect(body).toHaveBeenCalledWith('history', 'tool-0');
  fireEvent.click(screen.getByRole('button', { name: 'Load more activity' }));
  await screen.findByText('read_54');
  expect(listing).toHaveBeenLastCalledWith('history', 50);
  expect(body).toHaveBeenCalledTimes(1);
  view.unmount();
  const restored = render(<ConversationRunHistory run={run} />);
  expect(listing).toHaveBeenCalledTimes(2);
  open(restored.container.querySelector('.conversation-history')!);
  await screen.findByText('read_0');
  expect(listing).toHaveBeenLastCalledWith('history', 0);
});

it('loads thinking text only after disclosure and offers retry when a page fails', async () => {
  useLocale.setState({ locale: 'en' });
  const listing = vi.spyOn(worldApi, 'getRunActivity').mockRejectedValueOnce(new Error('temporary failure'))
    .mockResolvedValue({ items: [{ id: 'thought', type: 'agent_progress', kind: 'model_reasoning', provider_message_id: 'p' }],
      total: 1, next_offset: null });
  const text = vi.spyOn(worldApi, 'getRunReasoningText').mockResolvedValue({ run_id: 'history', provider_message_id: 'p',
    kind: 'model_reasoning', text: 'restored summary', text_length: 16, created_at: '', updated_at: '',
    streaming: false, interrupted: false, truncated: false, preview_truncated: false, revision: 1 });
  const view = render(<ConversationRunHistory run={run} />);
  open(view.container.querySelector('.conversation-history')!);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('Model thinking');
  expect(listing).toHaveBeenCalledTimes(2);
  expect(text).not.toHaveBeenCalled();
  open(view.container.querySelector('.run-model-reasoning')!);
  await waitFor(() => expect(screen.getByText('restored summary')).toBeTruthy());
  expect(text).toHaveBeenCalledTimes(1);
  expect(text).toHaveBeenCalledWith('history', 'p');
});
