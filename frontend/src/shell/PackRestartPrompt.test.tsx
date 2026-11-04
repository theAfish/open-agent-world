// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as application from '../state/applicationRestart';
import { PackRestartPrompt } from './PackRestartPrompt';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('starts the restart once and holds the dialog open while the server recovers', async () => {
  const restart = vi.spyOn(application, 'restartApplication').mockReturnValue(new Promise(() => {}));
  const close = vi.fn();
  render(<PackRestartPrompt onClose={close} />);
  const button = screen.getByRole('button', { name: 'Restart now' });
  fireEvent.click(button); fireEvent.click(button);
  expect(restart).toHaveBeenCalledOnce();
  expect(screen.getByRole('status').textContent).toContain('Restarting OAW');
  expect((screen.getByRole('button', { name: 'Restart later' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(close).not.toHaveBeenCalled();
});

it('shows a restart failure and allows retrying', async () => {
  const restart = vi.spyOn(application, 'restartApplication').mockRejectedValueOnce(new Error('Save failed'))
    .mockReturnValue(new Promise(() => {}));
  render(<PackRestartPrompt onClose={vi.fn()} />);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Restart now' })));
  expect(screen.getByRole('alert').textContent).toBe('Save failed');
  fireEvent.click(screen.getByRole('button', { name: 'Restart now' }));
  expect(restart).toHaveBeenCalledTimes(2);
});
