import { computeLayout } from './layout';
import type { LayoutRequest } from './types';
self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  try { self.postMessage({ result: computeLayout(event.data) }); }
  catch (error) { self.postMessage({ error: String(error) }); }
};
