// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { TEST_CATALOG } from '../state/catalog.fixture';
import type { WorldCard } from '../types/world';
import { TaskBoardBody, TaskBoardPreview, type BoardSnapshot } from './TaskBoard';
import { clearTaskBoardCache } from './taskBoardCache';

const card: WorldCard = { id: 'tasks', type: 'oaw.tasks', name: 'Tasks', status: 'available',
  config: {}, position: { x: 0, y: 0 }, size: { width: 360, height: 240 }, expanded: false };
const document = (revision = 1, title = 'Research'): BoardSnapshot => ({ revision,
  value: { tasks: [{ id: 'a', title, status: 'todo', description: 'Long instructions', depends_on: [], note: '' }],
    execution: { default_executor_id: null, max_parallel: 1, pause_on_failure: true } },
  summary: { total: 1, done: 0, ready_ids: ['a'] } });

beforeEach(() => {
  clearTaskBoardCache();
  useNodeSurfaceStore.setState({ drafts: {} });
  useWorldStore.setState({ cards: [card], catalog: TEST_CATALOG, events: [], socketState: 'live' });
  vi.spyOn(worldApi, 'getNodeDocument').mockResolvedValue(document());
  vi.spyOn(worldApi, 'getNodeDocumentSummary').mockResolvedValue({ revision: 1, summary: document().summary });
  vi.spyOn(worldApi, 'getNodeExecution').mockResolvedValue({ status: 'idle', active: false, error: null, executors: [], items: [], attempts: [] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('previews read only summaries and reuse them across fast remounts', async () => {
  const first = render(<TaskBoardPreview card={card} />);
  await screen.findByText('0 of 1 tasks complete');
  first.unmount();
  render(<TaskBoardPreview card={card} />);
  expect(screen.getByText('0 of 1 tasks complete')).toBeTruthy();
  expect(worldApi.getNodeDocumentSummary).toHaveBeenCalledTimes(1);
  expect(worldApi.getNodeDocument).not.toHaveBeenCalled();
  expect(worldApi.getNodeExecution).not.toHaveBeenCalled();
});

it('shares a concurrent inspector read with another inspector and preview', async () => {
  render(<><TaskBoardBody card={card} /><TaskBoardBody card={card} /><TaskBoardPreview card={card} /></>);
  await screen.findByText('0 of 1 tasks complete');
  expect(worldApi.getNodeDocument).toHaveBeenCalledTimes(1);
  expect(worldApi.getNodeDocumentSummary).not.toHaveBeenCalled();
});

it('retains the task draft and original revision after unmount and remote changes', async () => {
  const save = vi.spyOn(worldApi, 'nodeDocumentAction').mockResolvedValue(document(3));
  const view = render(<TaskBoardBody card={card} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit task Research' }));
  fireEvent.change(screen.getByLabelText('Task description'), { target: { value: 'My unsaved instructions' } });
  view.unmount();
  vi.mocked(worldApi.getNodeDocument).mockResolvedValue(document(2, 'Remote change'));
  act(() => useWorldStore.setState({ events: [{ id: 'changed', type: 'state_changed', timestamp: '', payload: { scope_kind: 'node_document', owner_id: card.id } }] }));
  render(<TaskBoardBody card={card} />);
  await screen.findByRole('button', { name: 'Edit task Remote change' });
  expect(screen.getByLabelText('Task description')).toHaveValue('My unsaved instructions');
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(card.id, 'upsert', { tasks: [expect.objectContaining({ description: 'My unsaved instructions' })] }, 1, null));
});

it('does no document or execution work and mounts no list while hidden', async () => {
  const view = render(<TaskBoardBody card={card} level="preview" />);
  expect(view.container).toBeEmptyDOMElement();
  await act(async () => {});
  expect(worldApi.getNodeDocument).not.toHaveBeenCalled();
  expect(worldApi.getNodeExecution).not.toHaveBeenCalled();
  view.rerender(<TaskBoardBody card={card} level="inspector" />);
  await screen.findByRole('button', { name: 'Edit task Research' });
  view.rerender(<TaskBoardBody card={card} level="preview" />);
  vi.useFakeTimers();
  await act(async () => { vi.advanceTimersByTime(10000); });
  expect(view.container).toBeEmptyDOMElement();
  expect(worldApi.getNodeDocument).toHaveBeenCalledTimes(1);
  expect(worldApi.getNodeExecution).toHaveBeenCalledTimes(1);
});

it('does not clear a new quick-add draft when an old mount finishes saving', async () => {
  let finish!: (next: BoardSnapshot) => void;
  vi.spyOn(worldApi, 'nodeDocumentAction').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const first = render(<TaskBoardBody card={card} />);
  await screen.findByRole('button', { name: 'Edit task Research' });
  fireEvent.change(screen.getByLabelText('New task title'), { target: { value: 'Submitted' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add task' }));
  first.unmount();
  render(<TaskBoardBody card={card} />);
  fireEvent.change(screen.getByLabelText('New task title'), { target: { value: 'Next draft' } });
  await act(async () => finish(document(2)));
  expect(screen.getByLabelText('New task title')).toHaveValue('Next draft');
});

it('replaces an older full read after a newer preview summary arrives without another world event', async () => {
  let finishSummary!: (next: { revision: number; summary: BoardSnapshot['summary'] }) => void;
  let finishFull!: (next: BoardSnapshot) => void;
  vi.mocked(worldApi.getNodeDocumentSummary).mockImplementation(() => new Promise(resolve => { finishSummary = resolve; }));
  vi.mocked(worldApi.getNodeDocument).mockImplementationOnce(() => new Promise(resolve => { finishFull = resolve; }))
    .mockResolvedValue(document(2, 'Latest task'));
  render(<><TaskBoardPreview card={card} /><TaskBoardBody card={card} /></>);
  await act(async () => finishSummary({ revision: 2, summary: document(2).summary }));
  expect(worldApi.getNodeDocument).toHaveBeenCalledTimes(1);
  await act(async () => finishFull(document(1, 'Old task')));
  await screen.findByRole('button', { name: 'Edit task Latest task' });
  expect(worldApi.getNodeDocument).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', { name: 'Edit task Old task' })).toBeNull();
});

it('falls back to a summary when a preview joined an inspector read that failed', async () => {
  let failFull!: (error: Error) => void;
  vi.mocked(worldApi.getNodeDocument).mockImplementation(() => new Promise((_resolve, reject) => { failFull = reject; }));
  render(<><TaskBoardBody card={card} /><TaskBoardPreview card={card} /></>);
  expect(worldApi.getNodeDocumentSummary).not.toHaveBeenCalled();
  await act(async () => failFull(new Error('Full document unavailable')));
  await screen.findByText('0 of 1 tasks complete');
  expect(screen.getByRole('alert')).toHaveTextContent('Full document unavailable');
  expect(worldApi.getNodeDocumentSummary).toHaveBeenCalledTimes(1);
  expect(worldApi.getNodeDocument).toHaveBeenCalledTimes(1);
});
