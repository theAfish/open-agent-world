import { Background, BackgroundVariant } from '@xyflow/react';
import { resolveBackground, useSkinCatalog } from '../appearance/registry';
import { useAppearance } from '../state/appearance';
import { TerrainBackground } from './TerrainBackground';

/** Backgrounds share the palette and leave all canvas gestures to React Flow. */
export function CanvasBackground() {
  const backgroundId = useAppearance(state => state.backgroundId);
  const { backgrounds } = useSkinCatalog();
  const background = resolveBackground(backgroundId, backgrounds);

  return <div className="canvas-background" data-background-kind={background.kind} aria-hidden="true"
    style={background.kind === 'gradient' ? {
      background: `linear-gradient(${background.angle ?? 135}deg, var(--canvas), var(--canvas-deep) 65%, var(--accent-soft))`,
    } : undefined}>
    {background.kind === 'terrain' && <TerrainBackground />}
    {(background.kind === 'dots' || background.kind === 'grid') && <Background
      id="skin-background"
      variant={background.kind === 'dots' ? BackgroundVariant.Dots : BackgroundVariant.Lines}
      gap={background.spacing ?? 24} size={1.5} lineWidth={0.75}
      color="var(--grid-dot)" bgColor="var(--canvas)" style={{ zIndex: 0 }} />}
  </div>;
}
