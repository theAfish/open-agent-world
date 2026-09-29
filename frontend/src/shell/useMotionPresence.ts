import { useLayoutEffect, useState } from 'react';

/** Keep the closing surface painted, while its owner disables interaction immediately. */
export function useMotionPresence(open: boolean, duration = 220) {
  const [retained, setRetained] = useState(open);
  useLayoutEffect(() => {
    if (open) { setRetained(true); return; }
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!media || media.matches) { setRetained(false); return; }
    const timer = window.setTimeout(() => setRetained(false), duration);
    const finish = () => { if (media.matches) setRetained(false); };
    media.addEventListener('change', finish);
    return () => { clearTimeout(timer); media.removeEventListener('change', finish); };
  }, [open, duration]);
  return { present: open || retained, closing: !open && retained };
}
