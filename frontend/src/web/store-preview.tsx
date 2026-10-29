import { createRoot } from 'react-dom/client';
import { StorePackPreview, type StorePreviewPack } from '../shell/StorePackPreview';

/** Small DOM adapter for the static Marketplace; rendering stays in OAW. */
export function mountPackPreview(host: HTMLElement, pack: StorePreviewPack, onClick: () => void, signal: AbortSignal) {
  if (signal.aborted) return;
  const root = createRoot(host);
  const update = (value: StorePreviewPack) => {
    if (!signal.aborted) root.render(<StorePackPreview pack={value} onClick={onClick} />);
  };
  signal.addEventListener('abort', () => root.unmount(), { once: true });
  update(pack);
  return { update };
}
