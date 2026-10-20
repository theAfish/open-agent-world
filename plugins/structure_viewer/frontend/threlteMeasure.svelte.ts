// Threlte 8.6 measures transformed screen bounds, then uses them as local CSS
// sizes for its canvas and HTML overlays. Inside React Flow that applies zoom
// twice. Keep the shared scene size in the same local space as pointer offsets.
// Canvas's host div has no padding or border; ResizeObserver preserves its
// fractional content dimensions without including ancestor transforms.
export function useMeasure(element: HTMLElement) {
  let size = $state.raw({ width: element.clientWidth, height: element.clientHeight });
  let renderedWidth = -1;
  let renderedHeight = -1;

  $effect(() => {
    const observer = new ResizeObserver(([entry]) => {
      size = { width: entry.contentRect.width, height: entry.contentRect.height };
    });
    observer.observe(element);
    return () => observer.disconnect();
  });

  return {
    size: { get current() { return size; } },
    shouldUpdateSize() {
      if (size.width === renderedWidth && size.height === renderedHeight) return false;
      renderedWidth = size.width;
      renderedHeight = size.height;
      return true;
    },
  };
}
