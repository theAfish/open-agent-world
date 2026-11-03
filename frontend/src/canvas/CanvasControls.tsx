import { ControlButton, Controls, useReactFlow, useViewport } from '@xyflow/react';
import { Maximize, Minus, Plus } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { MAX_CANVAS_ZOOM, MIN_CANVAS_ZOOM } from './useSmoothWheelZoom';

export function CanvasControls() {
  useLocale();
  return <Controls className="world-controls" position="bottom-right" showZoom={false} showFitView={false}
    showInteractive={false} aria-label={t('Canvas zoom controls')}><CanvasZoomButtons /></Controls>;
}

/** The phone toolbar and desktop panel use the same camera controls. */
export function CanvasZoomButtons() {
  useLocale();
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { zoom } = useViewport();
  const options = () => ({ duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 200 });
  return <>
    <ControlButton className="react-flow__controls-zoomin" title={t('Zoom in')} aria-label={t('Zoom in')}
      disabled={zoom >= MAX_CANVAS_ZOOM} onClick={() => void zoomIn(options())}><Plus /></ControlButton>
    <ControlButton className="react-flow__controls-zoomout" title={t('Zoom out')} aria-label={t('Zoom out')}
      disabled={zoom <= MIN_CANVAS_ZOOM} onClick={() => void zoomOut(options())}><Minus /></ControlButton>
    <ControlButton className="react-flow__controls-fitview" title={t('Fit view')} aria-label={t('Fit view')}
      onClick={() => void fitView(options())}><Maximize /></ControlButton>
  </>;
}
