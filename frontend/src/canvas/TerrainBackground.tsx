import { memo, useEffect, useRef, useState } from 'react';
import { useStoreApi } from '@xyflow/react';
import { useWorldStore } from '../state/worldStore';
import { TerrainRendererWebGL } from './TerrainRendererWebGL';

export const TerrainBackground = memo(function TerrainBackground() {
  const store = useStoreApi();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    const element = canvas.current!;
    let renderer: TerrainRendererWebGL;
    try {
      renderer = new TerrainRendererWebGL(element, (status, reason) => {
        if (reason) element.dataset.terrainError = reason;
        else delete element.dataset.terrainError;
        setStatus(status);
      });
    } catch (error) {
      element.dataset.terrainError = String(error);
      setStatus('unavailable');
      return;
    }
    const updateView = () => {
      const { transform: [x, y, zoom], width, height } = store.getState();
      renderer.setViewport({ x, y, zoom, width, height });
    };
    renderer.setSeed(useWorldStore.getState().terrainSeed);
    updateView();
    const unsubscribe = store.subscribe((state, previous) => {
      if (state.transform !== previous.transform || state.width !== previous.width || state.height !== previous.height) updateView();
    });
    const unsubscribeSeed = useWorldStore.subscribe((state, previous) => {
      if (state.terrainSeed !== previous.terrainSeed) renderer.setSeed(state.terrainSeed);
    });
    const theme = new MutationObserver(() => renderer.refreshTheme());
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });
    const resize = new ResizeObserver(updateView);
    resize.observe(element.parentElement!);
    const onResize = () => { updateView(); renderer.refreshSize(); };
    window.addEventListener('resize', onResize);
    // Monitor-only moves can change DPR without changing CSS viewport dimensions.
    let dpr: MediaQueryList;
    const watchDpr = () => {
      dpr?.removeEventListener('change', watchDpr);
      dpr = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      dpr.addEventListener('change', watchDpr);
      renderer.refreshSize();
    };
    watchDpr();
    return () => {
      unsubscribe(); unsubscribeSeed(); theme.disconnect(); resize.disconnect();
      window.removeEventListener('resize', onResize); dpr.removeEventListener('change', watchDpr);
      renderer.dispose();
    };
  }, [store]);

  // The shell's theme color remains visible when WebGL is unavailable or lost.
  // Keep the canvas mounted so a restored context can redraw automatically.
  return <canvas ref={canvas} className="terrain-webgl-background" data-terrain-renderer="webgl2"
    data-terrain-status={status} aria-hidden="true" style={{ visibility: status === 'ready' ? undefined : 'hidden' }} />;
});
