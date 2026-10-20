import { useEffect } from 'react';
import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { currentHostedSession, hostedSessionActive } from './hostedSession';

/** Refresh public choices without replacing an Agent's persisted model selection. */
export function useSharedModels() {
  useEffect(() => {
    if (!currentHostedSession()?.shared_models) return;
    let active = true, busy = false;
    const refresh = async () => {
      if (!active || busy || document.hidden || !hostedSessionActive()) return;
      busy = true;
      try {
        const catalog = await worldApi.getModelConnections();
        if (active && hostedSessionActive()) useWorldStore.setState(state => catalog.revision > state.modelCatalog.revision ? { modelCatalog: catalog } : {});
      } catch { /* The ordinary model request reports service errors; never erase a catalog on a poll failure. */ }
      finally { busy = false; }
    };
    const visible = () => { void refresh(); };
    const timer = setInterval(visible, 15_000);
    window.addEventListener('focus', visible);
    return () => { active = false; clearInterval(timer); window.removeEventListener('focus', visible); };
  }, []);
}
