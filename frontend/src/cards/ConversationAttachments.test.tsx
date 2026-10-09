// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { ConversationAttachments } from './ConversationAttachments';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('opens text without a connected viewer and renders active content as literal text', async () => {
  const text = '<script>alert("unsafe")</script>';
  vi.spyOn(worldApi, 'previewConversationAttachment').mockResolvedValue({ state: 'text', text });
  render(<ConversationAttachments conversationId="room" sessionId="session" files={[{ version_id: 'v1', path: 'report.html', name: 'report.html', size_bytes: 35, media_type: 'text/html' }]} />);
  const trigger = screen.getByRole('button', { name: 'Open report.html' });
  trigger.focus(); fireEvent.click(trigger);
  const content = await screen.findByText(text);
  expect(content.tagName).toBe('PRE'); expect(content.querySelector('script')).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Preview report.html' })).toBeTruthy();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(document.activeElement).toBe(trigger);
});
