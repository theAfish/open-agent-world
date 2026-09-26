import { useCallback, useEffect, useRef, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import type { PackInstallations } from '../types/packs';

export function usePackInstallations(active: boolean) {
  const [state, setState] = useState<PackInstallations>();
  const [error, setError] = useState('');
  const generation = useRef(0);
  const accept = useCallback((value: PackInstallations) => {
    generation.current++;
    setState(value); setError('');
  }, []);
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      const current = generation.current;
      try {
        const result = await worldApi.getInstalledPacks();
        if (!disposed && current === generation.current) { setState(result); setError(''); }
      } catch (cause) {
        if (!disposed && current === generation.current) setError(apiErrorMessage(cause));
      } finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [active]);
  return { state, error, accept };
}
