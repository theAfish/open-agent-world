/** Track a popup inside a moving, possibly scaled conversation workspace. */
export function observeConversationPopup(host: HTMLElement, anchor: HTMLElement, popup: HTMLElement,
  onClose: () => void, placement: 'below' | 'flip') {
  const position = () => {
    const origin = host.getBoundingClientRect();
    const rect = anchor.getBoundingClientRect();
    const scale = host.offsetWidth ? origin.width / host.offsetWidth || 1 : 1;
    const bottom = (rect.bottom - origin.top) / scale + 4;
    popup.style.maxWidth = `${Math.max(0, host.clientWidth - 8)}px`;
    popup.style.maxHeight = `${Math.max(0, host.clientHeight - (placement === 'below' ? bottom + 4 : 8))}px`;
    popup.style.left = `${Math.max(4, (rect.right - origin.left) / scale - popup.offsetWidth)}px`;
    const top = placement === 'flip' && bottom + popup.offsetHeight > host.clientHeight
      ? (rect.top - origin.top) / scale - popup.offsetHeight - 4 : bottom;
    popup.style.top = `${placement === 'flip' ? Math.max(4, top) : top}px`;
  };
  const outside = (event: PointerEvent) => {
    if (event.target instanceof Node && !anchor.contains(event.target) && !popup.contains(event.target)) onClose();
  };
  position();
  const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(position);
  observer?.observe(host);
  observer?.observe(popup);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  document.addEventListener('pointerdown', outside);
  return () => {
    observer?.disconnect();
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
    document.removeEventListener('pointerdown', outside);
  };
}
