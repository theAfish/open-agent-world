// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PluginViewProps } from './sdk';
import { TaskBoard } from '../../../plugins/matcreator/frontend/TaskBoard';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const task = { id: 'build', title: 'Build copper', description: '', status: 'pending', depends_on: [] as string[], result: '', outputs: [] as string[] };
const board = (revision: number, title = task.title) => ({ revision, value: { plans: [
  { id: 'research', title: 'Copper study', goal: 'Verify a structure', session_id: '', tasks: [{ ...task, title }] },
] } });

it('creates plans without session inputs and does not display legacy session labels', async () => {
  const state = board(1);
  state.value.plans[0].session_id = 'legacy-session-label';
  const action = vi.fn().mockResolvedValue(board(2));
  render(<TaskBoard {...{ host: { readDocument: vi.fn().mockResolvedValue(state), documentAction: action } } as unknown as PluginViewProps} />);
  await screen.findByRole('heading', { name: 'Copper study' });
  fireEvent.click(screen.getByLabelText('Plan details and actions'));
  expect(screen.queryByText(/legacy-session-label/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'New plan' }));
  expect(screen.queryByLabelText(/Session reference/)).toBeNull();
  fireEvent.change(screen.getByLabelText('Plan title'), { target: { value: 'New study' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create plan' }));
  await waitFor(() => expect(action).toHaveBeenCalledWith('create_plan', { title: 'New study', goal: '', tasks: [] }, 1));
});

it('shows executor evidence and sends a targeted stop without silently accepting a result', async () => {
  const state = board(1);
  const collect = { document: state, execution: {
    items: [{ id: 'work-a', metadata: { plan_id: 'research', task_id: 'build' } }],
    attempts: [{ item_id: 'work-a', instance_id: 'instance-a', run_id: 'run-a', agent_id: 'executor-a',
      status: 'running', output_directory: 'research/a/attempt-1', text: '' }],
  } };
  const delegationAction = vi.fn().mockResolvedValue(collect);
  const documentAction = vi.fn();
  render(<TaskBoard {...{ host: { delegationAction, documentAction } } as unknown as PluginViewProps} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Build copper' }));
  expect(screen.getByText('research/a/attempt-1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Stop task' }));
  await waitFor(() => expect(delegationAction).toHaveBeenCalledWith('stop', { instance_id: 'instance-a' }));
  expect(documentAction).not.toHaveBeenCalled();
});

it('retains an editor draft when the agent updates the board and reloads explicitly', async () => {
  const read = vi.fn().mockResolvedValue(board(1));
  const action = vi.fn();
  render(<TaskBoard {...{ host: { readDocument: read, documentAction: action } } as unknown as PluginViewProps} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Build copper' }));
  expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Task title'), { target: { value: 'My unsaved edit' } });
  read.mockResolvedValue(board(2, 'Agent changed title'));
  fireEvent.click(screen.getByLabelText('Plan details and actions'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await screen.findByText('The board changed while you were editing. Your draft is retained.');
  expect((screen.getByLabelText('Task title') as HTMLInputElement).value).toBe('My unsaved edit');
  expect((screen.getByRole('button', { name: 'Save task' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Reload latest task' }));
  expect((screen.getByLabelText('Task title') as HTMLInputElement).value).toBe('Agent changed title');
  action.mockResolvedValue(board(3, 'Final title'));
  fireEvent.change(screen.getByLabelText('Task title'), { target: { value: 'Final title' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await screen.findByRole('heading', { name: 'Final title' });
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(action).toHaveBeenCalledWith('update_task', expect.objectContaining({ plan_id: 'research', task_id: 'build', title: 'Final title' }), 2);
});

it('keeps the draft after a rejected dependency transition', async () => {
  const action = vi.fn().mockRejectedValue(new Error('Complete prerequisite tasks first'));
  render(<TaskBoard {...{ host: { readDocument: vi.fn().mockResolvedValue(board(1)), documentAction: action } } as unknown as PluginViewProps} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Build copper' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Task status'), { target: { value: 'running' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Complete prerequisite'));
  expect((screen.getByLabelText('Task status') as HTMLSelectElement).value).toBe('running');
});

it('prioritizes active work and exposes every task as a card by default', async () => {
  const tasks = ['pending', 'done', 'running', 'blocked', 'running', 'done', 'done', 'done'].map((status, index) => ({
    ...task, id: String(index), title: `Task ${index}`, status, description: 'Long task detail', result: status === 'blocked' ? 'Waiting for allocation' : '',
  }));
  const state = board(1);
  state.value.plans[0].tasks = tasks;
  const { container } = render(<TaskBoard {...{ host: { readDocument: vi.fn().mockResolvedValue(state) } } as unknown as PluginViewProps} />);
  await screen.findByRole('button', { name: 'Task 2' });
  expect([...container.querySelectorAll('.mc-task-section')].map(section => section.getAttribute('aria-label'))).toEqual(['Running', 'Needs attention', 'Next', 'Done']);
  expect(screen.getByText('Waiting for allocation')).toBeTruthy();
  expect(screen.queryByText('Long task detail')).toBeNull();
  expect(container.querySelectorAll('.mc-task-item')).toHaveLength(8);
  expect(screen.queryByRole('button', { name: /Show all|View all tasks/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Task 7' }));
  expect(screen.getByText('Long task detail')).toBeTruthy();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(container.querySelector('.mc-task-grid')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '‹ Tasks' }));
  expect(container.querySelectorAll('.mc-task-item')).toHaveLength(8);
});

it.each(['empty', 'done', 'blocked'])('omits empty activity sections for %s plans', async status => {
  const state = board(1);
  state.value.plans[0].tasks = status === 'empty' ? [] : [{ ...task, status, result: 'Verified' }];
  const { container } = render(<TaskBoard {...{ host: { readDocument: vi.fn().mockResolvedValue(state) } } as unknown as PluginViewProps} />);
  await screen.findByRole('heading', { name: 'Copper study' });
  expect(container.querySelectorAll('.mc-task-section')).toHaveLength(status === 'empty' ? 0 : 1);
  if (status === 'done') expect(screen.getByText('1/1 ✓')).toBeTruthy();
  if (status === 'empty') expect(screen.getByText('No tasks yet. Add a task to get started.')).toBeTruthy();
});

it('keeps read-only details live, shows dependency state and handles removal', async () => {
  const state = board(1);
  state.value.plans[0].tasks = [
    { ...task, status: 'blocked', depends_on: ['setup'], result: 'Waiting for environment' },
    { ...task, id: 'setup', title: 'Environment setup', status: 'pending' },
  ];
  const read = vi.fn().mockResolvedValue(state);
  const action = vi.fn();
  render(<TaskBoard {...{ host: { readDocument: read, documentAction: action } } as unknown as PluginViewProps} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Build copper' }));
  expect(screen.getByRole('article').textContent).toContain('Environment setup');
  expect(screen.getByText('Blocking reason')).toBeTruthy();
  expect(screen.getByRole('article').querySelector('input, select, textarea')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Task title'), { target: { value: 'Discarded draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('heading', { name: 'Build copper' })).toBeTruthy();
  expect(action).not.toHaveBeenCalled();
  read.mockResolvedValue({ ...state, revision: 2, value: { plans: [{ ...state.value.plans[0], tasks: state.value.plans[0].tasks.map(item => ({ ...item, status: 'done', result: 'Verified' })) }] } });
  fireEvent.click(screen.getByLabelText('Plan details and actions'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await screen.findByText('Verified');
  expect(screen.getByRole('article').textContent).toContain('Environment setupDone');
  read.mockResolvedValue({ revision: 3, value: { plans: [{ ...state.value.plans[0], tasks: [] }] } });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await screen.findByText('Task no longer exists');
  expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '‹ Tasks' }));
  expect(screen.getByText('No tasks yet. Add a task to get started.')).toBeTruthy();
});
