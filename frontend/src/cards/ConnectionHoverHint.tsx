import { type PointerEvent as ReactPointerEvent } from "react";
import { roundedRectAnchor } from "../edges/geometry";

export function clearConnectionHoverHint(element: HTMLElement | null) {
  element?.removeAttribute("data-connection-hot");
}

/** Updates the shared, boundary-projected connection affordance for any card surface. */
export function updateConnectionHoverHint(event: ReactPointerEvent<HTMLElement>, element: HTMLElement | null) {
  if (!element) return;
  if ((event.target as Element).closest("button, input, textarea, select, a")) {
    clearConnectionHoverHint(element);
    return;
  }
  const bounds = element.getBoundingClientRect();
  const scaleX = element.offsetWidth / Math.max(bounds.width, 1);
  const scaleY = element.offsetHeight / Math.max(bounds.height, 1);
  const pointer = {
    x: (event.clientX - bounds.left) * scaleX,
    y: (event.clientY - bounds.top) * scaleY,
  };
  const cornerRadius = Number.parseFloat(window.getComputedStyle(element).borderTopLeftRadius) || 0;
  const anchor = roundedRectAnchor(
    { x: 0, y: 0, width: element.offsetWidth, height: element.offsetHeight },
    pointer,
    cornerRadius,
  );
  if (Math.hypot(pointer.x - anchor.x, pointer.y - anchor.y) > 20 * Math.max(scaleX, scaleY)) {
    clearConnectionHoverHint(element);
    return;
  }
  element.style.setProperty("--connection-hint-x", `${anchor.x}px`);
  element.style.setProperty("--connection-hint-y", `${anchor.y}px`);
  element.dataset.connectionHot = "true";
}

export function ConnectionHoverHint() {
  return <svg className="connection-hover-hint" data-connection-hover-hint viewBox="0 0 12 12" aria-hidden="true">
    <circle className="semantic-edge-endpoint" cx="6" cy="6" r="4.5" />
  </svg>;
}
