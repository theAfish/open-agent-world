import { normalizeCardFinish, type CardFinish } from './cardFinish';
import { CardMaterialCanvas, type CardMaterialOptions } from './CardMaterialCanvas';
import { useMemo } from 'react';
import { compileProduction, compileProductionLayer, hasFinishing, NO_FINISHING, productionForFinish } from './cardProduction';
import './cardFinish.css';

export type CardFinishQuality = 'thumbnail' | 'standard' | 'showcase';
/** Chrome is retained as a compatibility guard: application surfaces never mount materials. */
export type CardFinishSurface = 'card' | 'chrome';

export function CardFinishLayer({ finish, quality = 'standard', reveal = false, surface = 'card', restrained = false, roughness, ...options }: CardMaterialOptions & {
  finish?: CardFinish;
  quality?: CardFinishQuality;
  reveal?: boolean;
  surface?: CardFinishSurface;
  restrained?: boolean;
  roughness?: number;
}) {
  const material = normalizeCardFinish(finish);
  const compiled = useMemo(() => compileProduction(options.production ?? productionForFinish(material)), [material, options.production]);
  const definition = options.material ?? compiled.material;
  const finishing = options.finishing ?? (options.material && !options.production ? NO_FINISHING : compiled.finishing);
  if (surface === 'chrome') return null;
  // Each process has its own plate and optical pass. DOM order is physical order;
  // repeated processes remain distinct and a later ink/foil pass covers earlier film.
  if (options.production?.layers !== undefined) {
    return <>{options.production.layers.map((layer, index) => {
      if (!layer.enabled || layer.strength <= 0 || (layer.kind === 'ink' && layer.content)
        || (options.debugView === 'artwork' && layer.kind !== 'ink')
        || (options.debugView === 'laminate' && layer.kind !== 'laminate')
        || (options.debugView === 'finishing' && (layer.kind === 'laminate' || layer.kind === 'ink'))) return null;
      const pass = compileProductionLayer(layer);
      return <span key={layer.id} className={`card-finish-layer card-finish-layer--${material} card-process-layer`}
        data-finish={material} data-quality={quality} data-material-surface={surface} data-material-layer="process"
        style={{ ...(options.production?.print.layered ? { zIndex: index + 2 } : {}), mixBlendMode: layer.kind === 'ink' ? layer.blend : undefined }} data-process-layer={layer.id} data-process-kind={layer.kind} data-process-order={index}
        data-process-mask={layer.mask.source} data-process-relief={layer.kind === 'emboss' ? layer.relief : undefined}
        data-material-id={pass.material.id} data-material-debug={options.debugView ?? 'composite'}
        data-reveal={reveal || undefined} aria-hidden="true">
        <CardMaterialCanvas finish={material} quality={quality} restrained={restrained} {...options}
          material={pass.material} finishing={pass.finishing} processLayer={layer} />
      </span>;
    })}</>;
  }
  // The zero-energy normal preset may elide its canvas outside the inspector.
  if (!options.debugView && !hasFinishing(finishing) && !definition.laminate.opacity && !definition.clearcoat.strength && !definition.response.sparkle) return null;
  return <span className={`card-finish-layer card-finish-layer--${material}`}
    data-finish={material} data-quality={quality} data-material-surface={surface} data-material-layer="laminate"
    data-material-id={definition.id} data-material-debug={options.debugView ?? 'composite'}
    data-reveal={reveal || undefined} aria-hidden="true">
    <CardMaterialCanvas finish={material} quality={quality} restrained={restrained} roughness={roughness} {...options} material={definition} finishing={finishing} />
  </span>;
}
