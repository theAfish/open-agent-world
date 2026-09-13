import { Background, BackgroundVariant, useStore } from '@xyflow/react';

/** Keep dots visible in screen pixels; zooming out shows a coarser world grid. */
export function WorldBackground() {
  const zoom = useStore(state => state.transform[2]);
  const stride = 2 ** Math.max(0, Math.ceil(Math.log2(18 / (24 * zoom))));
  return <Background id="oaw-world-grid" variant={BackgroundVariant.Dots}
    gap={24 * stride} size={2 / zoom} color="var(--grid-dot)" />;
}
