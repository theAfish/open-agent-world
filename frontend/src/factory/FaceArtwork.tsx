import { useId, type CSSProperties, type ReactNode } from 'react';
import { CatalogIcon } from '../components/CatalogIcon';
import { CardFinishLayer } from '../cards/CardFinishLayer';
import { useCardFinish } from '../cards/useCardFinish';
import { MATERIAL_FINISH, designTokens, readableInk, recipeSettings, recipeProduction } from './designRecipes';
import { stockStyle, printContentIds, resolveElementCoverage, type PrintProof } from '../cards/cardProduction';
import { WorldCardPrint } from '../cards/WorldCardPrint';
import { InkPrintLayer } from './InkPrintLayer';
import { FaceFunctionElement } from './FaceFunctionElements';
import '../components/cardFace.css';
import type { FaceDesign, FaceElement, FaceShape, FunctionDesign, SurfaceDesign } from './types';
import type { NodeSurfaceLevel } from '../types/world';
import './factory.css';
import './recipeArtwork.css';

export function ShapePath({ shape, normalize, source = false }: { shape: FaceShape; source?: boolean; normalize?: { width: number; height: number } }) {
  const sx = normalize?.width ?? 1, sy = normalize?.height ?? 1;
  const x = shape.x / sx, y = shape.y / sy, width = shape.width / sx, height = shape.height / sy;
  // A printing pass changes ink opacity, never the die-cut outline used for clipping.
  const style: CSSProperties | undefined = normalize ? undefined : { opacity: shape.print?.opacity, mixBlendMode: shape.print?.blend };
  const plateId = normalize ? undefined : shape.id;
  if (shape.kind === 'ellipse') return <ellipse data-face-shape={source ? undefined : plateId} data-ink-shape={source ? plateId : undefined} cx={x + width / 2} cy={y + height / 2} rx={width / 2} ry={height / 2} fill={shape.fill} style={style} />;
  if (shape.kind === 'polygon') return <polygon data-face-shape={source ? undefined : plateId} data-ink-shape={source ? plateId : undefined} points={shape.points.map(point => `${x + point.x * width},${y + point.y * height}`).join(' ')} fill={shape.fill} style={style} />;
  return <rect data-face-shape={source ? undefined : plateId} data-ink-shape={source ? plateId : undefined} x={x} y={y} width={width} height={height} rx={shape.radius / sx} ry={shape.radius / sy} fill={shape.fill} style={style} />;
}

export function elementStyle(element: FaceElement, surface: SurfaceDesign): CSSProperties {
  return { left: `${element.x / surface.width * 100}%`, top: `${element.y / surface.height * 100}%`, width: `${element.width / surface.width * 100}%`,
    height: `${element.height / surface.height * 100}%`, fontSize: element.font_size, color: element.color, textAlign: element.align,
    opacity: element.print?.opacity, mixBlendMode: element.print?.blend };
}

export function FaceArtwork({ face, surface, level, functionDesign, render, thumbnail = false, interactive = true, children, proof = 'composite' }: {
  face: FaceDesign; surface: SurfaceDesign; level?: NodeSurfaceLevel; functionDesign?: FunctionDesign; render?: (element: FaceElement) => ReactNode | undefined; sample?: boolean; thumbnail?: boolean; interactive?: boolean; children?: ReactNode; proof?: PrintProof;
}) {
  const id = `face-${useId().replaceAll(':', '')}`;
  const png = surface.background_png, alpha = png && surface.image_shape;
  const design = surface.design, tokens = design ? designTokens(surface) : undefined;
  const resolved = recipeSettings(face, surface), material = resolved.material, finish = MATERIAL_FINISH[material.type];
  const authored = recipeProduction(resolved);
  const contentIds = [...surface.elements, ...surface.shapes.slice(1)].map(item => item.id);
  const production = authored.layers ? { ...authored, layers: authored.layers.map(layer => resolveElementCoverage(layer, authored.layers!, contentIds)) } : authored;
  const lighting = useCardFinish(interactive && !thumbnail && proof !== 'artwork' ? 'foil' : 'normal', 'standard');
  const style = tokens ? { '--face-surface': tokens.surface, '--face-ink': tokens.text, '--face-border': tokens.border, '--face-accent': readableInk(tokens.surface, face.color),
    '--face-radius': `${tokens.radius}px`, '--face-softness': design!.softness, '--face-action-ink': readableInk(face.color, '#ffffff') } as CSSProperties : undefined;
  const fit = surface.image_fit === 'stretch' ? '100% 100%' : surface.image_fit;
  const clip: CSSProperties = alpha ? { maskImage: `url("${png}")`, maskSize: fit, maskPosition: 'center', maskRepeat: 'no-repeat', maskMode: 'alpha' }
    : { clipPath: `url(#${id})` };
  const layered = Boolean(production.print.layered), layers = production.layers ?? [];
  const background = (source = false) => png ? <img className={source ? 'factory-ink-source-background' : 'factory-background'} data-material-layer="artwork" data-material-region="artwork" src={png} alt="" draggable={false} style={{ objectFit: surface.image_fit === 'stretch' ? 'fill' : surface.image_fit }} /> : design ? <WorldCardPrint motif={production.print.motif} fullBleed /> : null;
  const shapes = (items: FaceShape[], source = false) => <svg className={source ? 'factory-ink-source-shapes' : 'factory-shapes'} viewBox={`0 0 ${surface.width} ${surface.height}`} preserveAspectRatio="none" aria-hidden="true">{items.map(shape => <ShapePath key={shape.id} shape={shape} source={source} />)}</svg>;
  const elements = (items: FaceElement[], source = false) => items.map((element, index) => <div key={element.id} data-face-element={source ? undefined : element.id} data-kind={source ? undefined : element.kind} data-ink-element={source ? element.id : undefined} data-ink-kind={source ? element.kind : undefined}
        data-material-layer={element.kind === 'illustration' ? 'artwork' : 'protected'}
        data-material-region={element.kind === 'illustration' ? 'artwork' : 'text'}
        className="factory-art-element" style={{ ...elementStyle(element, surface), zIndex: index + 2 }}>
        {(source ? undefined : render?.(element)) ?? (element.kind === 'icon' ? <CatalogIcon definition={{ icon: face.icon }} size={Math.min(element.width, element.height, element.font_size * 1.5)} />
          : element.kind === 'title' ? <strong>{face.title}</strong>
          : element.kind === 'description' ? face.description
          : element.kind === 'help' ? face.help_text
          : element.kind === 'illustration' ? element.image_png ? <img className="factory-illustration" src={element.image_png} alt={element.text || ''} draggable={false} /> : <span className="factory-illustration-placeholder" aria-label="插图占位"><CatalogIcon definition={{ icon: 'layers' }} size={30} /></span>
          : element.kind === 'tags' ? <span className="factory-tags" style={{ justifyContent: element.align === 'center' ? 'center' : element.align === 'right' ? 'flex-end' : 'flex-start' }}>{element.text.split(/[,，]/).filter(tag => tag.trim()).map((tag, i) => <span key={i}>{tag.trim()}</span>)}</span>
          : element.kind === 'badge' || element.kind === 'status' ? <span className={`factory-semantic-${element.kind}`}>{element.text}</span>
          : ['text', 'subtitle', 'metadata'].includes(element.kind) ? element.text
          : <FaceFunctionElement element={element} face={face} surface={surface} level={level} fields={functionDesign?.fields} thumbnail={thumbnail || source} />)}
      </div>);
  return <div className="factory-artwork card-finish-surface" data-material-layer="substrate" data-card-stock={production.stock.type} data-print-proof={proof} data-preset={surface.preset} data-tone={surface.tone} data-image-shape={Boolean(alpha)} data-recipe={design?.recipe} data-kit={design?.kit} style={{ ...stockStyle(production), ...style, '--face-finish-order': surface.elements.length + 2 } as CSSProperties} {...lighting}>
    <svg width="0" height="0" className="factory-clip-defs" aria-hidden="true"><defs><clipPath id={id} clipPathUnits="objectBoundingBox">
      {surface.shapes.map(shape => <ShapePath key={shape.id} shape={shape} normalize={surface} />)}
    </clipPath></defs></svg>
    {alpha && !layered && <img className="factory-background" data-material-layer="artwork" data-material-region="artwork" src={png} alt="" draggable={false} style={{ objectFit: surface.image_fit === 'stretch' ? 'fill' : surface.image_fit }} />}
    <div className="factory-artwork-skin" style={clip}>
      {layered ? <>
        {!alpha && shapes(surface.shapes.slice(0, 1))}
        <div className="factory-paper-grain" aria-hidden="true" />
        <div className="factory-ink-source" data-ink-source aria-hidden="true">{shapes(surface.shapes.slice(1), true)}{background(true)}{elements(surface.elements, true)}</div>
        {layers.map((layer, index) => {
          if (layer.kind !== 'ink' || !layer.content) return null;
          const ids = printContentIds(layer, layers, [...surface.elements, ...surface.shapes.slice(1)].map(item => item.id));
          return <InkPrintLayer key={layer.id} layer={layer} order={index} hidden={proof === 'finishing' || proof === 'laminate'}>
            {shapes(surface.shapes.slice(1).filter(shape => ids.includes(shape.id)))}
            {layer.pattern ? <div className="factory-layer-pattern" style={{ '--print-density': layer.pattern.density, '--face-accent': layer.color } as CSSProperties}><WorldCardPrint motif={layer.pattern.motif} fullBleed /></div> : layer.content.source === 'all' && background()}
            {elements(surface.elements.filter(element => ids.includes(element.id)))}
          </InkPrintLayer>;
        })}
      </> : <>
        {!alpha && shapes(surface.shapes)}
        {(!alpha || !png) && background()}
        <div className="factory-paper-grain" aria-hidden="true" />
        {elements(surface.elements)}
      </>}
      {!thumbnail && <CardFinishLayer finish={finish} quality="standard" production={production} debugView={proof === 'composite' ? undefined : proof} />}

    </div>
    {children}
  </div>;
}
