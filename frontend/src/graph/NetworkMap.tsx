import { forwardRef, lazy, Suspense } from 'react';
import type { NetworkMapHandle } from './types';
import type { NetworkMapProps } from './NetworkMapImpl';
const Map = lazy(() => import('./NetworkMapImpl'));
/** Shared by bundled and installed Packs through the host SDK. */
export const NetworkMap = forwardRef<NetworkMapHandle, NetworkMapProps>((props, ref) =>
  <Suspense fallback={<div role="status">Loading map…</div>}><Map {...props} ref={ref} /></Suspense>);
NetworkMap.displayName = 'NetworkMap';
export type { NetworkMapProps } from './NetworkMapImpl';
