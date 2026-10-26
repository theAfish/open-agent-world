// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ConfirmDialog } from './ConfirmDialog';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('starts on Cancel, traps focus, and retains failed actions for retry', async () => {
  const run = vi.fn().mockRejectedValueOnce(new Error('Session is busy')).mockResolvedValue(undefined), close = vi.fn();
  render(<ConfirmDialog confirmation={{ title: 'Delete group?', items: ['Session A', 'Session B'], action: 'Delete group', run }} onClose={close} />);
  // jsdom has no layout; give the visible buttons rectangles for Modal's focus filter.
  for (const button of screen.getAllByRole('button')) {
    vi.spyOn(button, 'getClientRects').mockReturnValue([new DOMRect(0, 0, 80, 24)] as unknown as DOMRectList);
  }
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByText('Session B')).toBeTruthy();
  const remove = screen.getByRole('button', { name: 'Delete group' });
  fireEvent.click(remove);
  expect((await screen.findByRole('alert')).textContent).toContain('Session is busy');
  expect(close).not.toHaveBeenCalled();
  remove.focus(); fireEvent.keyDown(document, { key: 'Tab' });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));
  fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(remove);
  fireEvent.click(remove);
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
});
it('renders inside the active native dialog and restores the trigger focus', () => {
  const host = document.createElement('dialog'); host.setAttribute('open', '');
  const trigger = document.createElement('button'); host.append(trigger); document.body.append(host); trigger.focus();
  const view = render(<ConfirmDialog confirmation={{ title: 'Remove?', action: 'Remove', run: vi.fn() }} onClose={vi.fn()} />);
  expect(host.contains(screen.getByRole('dialog', { name: 'Remove?' }))).toBe(true);
  view.unmount(); expect(document.activeElement).toBe(trigger); host.remove();
});
