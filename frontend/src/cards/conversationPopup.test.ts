// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { observeConversationPopup } from './conversationPopup';

it.each([
  ['below', '164px', '32px'],
  ['flip', '76px', '192px'],
] as const)('positions a %s popup inside a scaled workspace and releases its listeners', (placement, top, maxHeight) => {
  const host = document.createElement('div');
  const anchor = document.createElement('button');
  const popup = document.createElement('div');
  host.append(anchor, popup);
  document.body.append(host);
  Object.defineProperties(host, { offsetWidth: { value: 300 }, clientWidth: { value: 300 }, clientHeight: { value: 200 } });
  Object.defineProperties(popup, { offsetWidth: { value: 100 }, offsetHeight: { value: 60 } });
  const origin = vi.spyOn(host, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 50, width: 150 } as DOMRect);
  vi.spyOn(anchor, 'getBoundingClientRect').mockReturnValue({ right: 200, top: 120, bottom: 130 } as DOMRect);
  const onClose = vi.fn();
  const stop = observeConversationPopup(host, anchor, popup, onClose, placement);
  try {
    expect(popup.style.left).toBe('100px');
    expect(popup.style.top).toBe(top);
    expect(popup.style.maxWidth).toBe('292px');
    expect(popup.style.maxHeight).toBe(maxHeight);
    origin.mockReturnValue({ left: 110, top: 50, width: 150 } as DOMRect);
    window.dispatchEvent(new Event('scroll'));
    expect(popup.style.left).toBe('80px');
    popup.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(onClose).not.toHaveBeenCalled();
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(onClose).toHaveBeenCalledOnce();
    stop();
    origin.mockReturnValue({ left: 120, top: 50, width: 150 } as DOMRect);
    window.dispatchEvent(new Event('resize'));
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(popup.style.left).toBe('80px');
    expect(onClose).toHaveBeenCalledOnce();
  } finally {
    stop();
    host.remove();
  }
});
