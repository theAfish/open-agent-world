import { useId, type CSSProperties, type ReactNode } from 'react';
import { CatalogIcon } from '../components/CatalogIcon';
import { CardFinishLayer } from '../cards/CardFinishLayer';
import { useCardFinish } from '../cards/useCardFinish';
import { MATERIAL_FINISH, STYLE_KITS, designTokens, readableInk, recipeSettings } from './designRecipes';
import type { FaceDesign, FaceElement, FaceShape, SurfaceDesign } from './types';
import './recipeArtwork.css';

export function ShapePath({ shape, normalize }: { shape: FaceShape; normalize?: { width: number; height: number } }) {
  const sx = normalize?.width ?? 1, sy = normalize?.height ?? 1;
  const x = shape.x / sx, y = shape.y / sy, width = shape.width / sx, height = shape.height / sy;
  if (shape.kind === 'ellipse') return <ellipse cx={x + width / 2} cy={y + height / 2} rx={width / 2} ry={height / 2} fill={shape.fill} />;
  if (shape.kind === 'polygon') return <polygon points={shape.points.map(point => `${x + point.x * width},${y + point.y * height}`).join(' ')} fill={shape.fill} />;
  return <rect x={x} y={y} width={width} height={height} rx={shape.radius / sx} ry={shape.radius / sy} fill={shape.fill} />;
}

export function elementStyle(element: FaceElement, surface: SurfaceDesign): CSSProperties {
  return { left: `${element.x / surface.width * 100}%`, top: `${element.y / surface.height * 100}%`, width: `${element.width / surface.width * 100}%`,
    height: `${element.height / surface.height * 100}%`, fontSize: element.font_size, color: element.color, textAlign: element.align };
}

export function FaceArtwork({ face, surface, render, sample = false, thumbnail = false, interactive = true, children }: {
  face: FaceDesign; surface: SurfaceDesign; render?: (element: FaceElement) => ReactNode | undefined; sample?: boolean; thumbnail?: boolean; interactive?: boolean; children?: ReactNode;
}) {
  const id = `face-${useId().replaceAll(':', '')}`;
  const png = surface.background_png, alpha = png && surface.image_shape;
  const design = surface.design, tokens = design ? designTokens(surface) : undefined;
  const resolved = recipeSettings(face, surface), material = resolved.material, finish = MATERIAL_FINISH[material.type];
  const lighting = useCardFinish(interactive && !thumbnail ? finish : 'normal', 'standard');
  const visuals = surface.elements.filter(e => e.kind === 'icon' || e.kind === 'illustration');
  const mask = material?.mask === 'edges' ? 'radial-gradient(ellipse at center, transparent 38%, #000 95%)'
    : material?.mask === 'visual' ? visuals.length ? visuals.map(e => `radial-gradient(ellipse ${Math.max(e.width, surface.width * .25)}px ${Math.max(e.height, surface.height * .2)}px at ${e.x + e.width / 2}px ${e.y + e.height / 2}px, #000, transparent)`).join(',') : 'linear-gradient(#000, transparent 65%)'
    : undefined;
  const style = tokens ? { '--face-surface': tokens.surface, '--face-ink': tokens.text, '--face-border': tokens.border, '--face-accent': readableInk(tokens.surface, face.color),
    '--face-radius': `${tokens.radius}px`, '--face-softness': design!.softness, '--face-action-ink': readableInk(face.color, '#ffffff') } as CSSProperties : undefined;
  const fit = surface.image_fit === 'stretch' ? '100% 100%' : surface.image_fit;
  const clip: CSSProperties = alpha ? { maskImage: `url("${png}")`, maskSize: fit, maskPosition: 'center', maskRepeat: 'no-repeat', maskMode: 'alpha' }
    : { clipPath: `url(#${id})` };
  return <div className="factory-artwork card-finish-surface" data-preset={surface.preset} data-tone={surface.tone} data-image-shape={Boolean(alpha)} data-recipe={design?.recipe} data-kit={design?.kit} style={style} {...lighting}>
    <svg width="0" height="0" className="factory-clip-defs" aria-hidden="true"><defs><clipPath id={id} clipPathUnits="objectBoundingBox">
      {surface.shapes.map(shape => <ShapePath key={shape.id} shape={shape} normalize={surface} />)}
    </clipPath></defs></svg>
    {alpha && <img className="factory-background" src={png} alt="" draggable={false} style={{ objectFit: surface.image_fit === 'stretch' ? 'fill' : surface.image_fit }} />}
    <div className="factory-artwork-skin" style={clip}>
      {!alpha && <svg className="factory-shapes" viewBox={`0 0 ${surface.width} ${surface.height}`} preserveAspectRatio="none" aria-hidden="true">
        {surface.shapes.map(shape => <ShapePath key={shape.id} shape={shape} />)}</svg>}
      {png && !alpha && <img className="factory-background" src={png} alt="" draggable={false} style={{ objectFit: surface.image_fit === 'stretch' ? 'fill' : surface.image_fit }} />}
      {!thumbnail && <div className="factory-material" data-material={material.type} data-restrained="true" style={{ opacity: material.intensity * STYLE_KITS[resolved.kit].response, maskImage: mask }}>
        {material.type === 'matte' ? <span className="factory-matte-surface" /> : <CardFinishLayer finish={finish} quality="standard" restrained roughness={material.roughness} />}
      </div>}
      {surface.elements.map(element => <div key={element.id} data-face-element={element.id} data-kind={element.kind}
        className="factory-art-element" style={elementStyle(element, surface)}>
        {render?.(element) ?? (element.kind === 'icon' ? <CatalogIcon definition={{ icon: face.icon }} size={Math.min(element.width, element.height, element.font_size * 1.5)} />
          : element.kind === 'title' ? <strong>{face.title}</strong>
          : element.kind === 'description' ? face.description
          : element.kind === 'help' ? face.help_text || (sample ? '在这里放置操作提示' : '')
          : element.kind === 'illustration' ? element.image_png ? <img className="factory-illustration" src={element.image_png} alt={element.text || ''} draggable={false} /> : <span className="factory-illustration-placeholder" aria-label="插图占位"><CatalogIcon definition={{ icon: 'layers' }} size={30} /></span>
          : element.kind === 'tags' ? <span className="factory-tags" style={{ justifyContent: element.align === 'center' ? 'center' : element.align === 'right' ? 'flex-end' : 'flex-start' }}>{element.text.split(/[,，]/).filter(tag => tag.trim()).map((tag, i) => <span key={i}>{tag.trim()}</span>)}</span>
          : element.kind === 'badge' || element.kind === 'status' ? <span className={`factory-semantic-${element.kind}`}>{element.text}</span>
          : ['text', 'subtitle', 'metadata'].includes(element.kind) ? element.text
          : element.kind === 'fields' ? <div className="factory-sample-fields" data-layout={surface.field_layout}><span>输入字段 A</span><span>输入字段 B</span></div>
          : element.kind === 'action' ? <span className="factory-sample-action" style={{ background: face.color }}>{face.button_label}</span>
          : <span className="factory-sample-result">{sample ? '运行结果显示在这里' : ''}</span>)}
      </div>)}
    </div>
    {children}
  </div>;
}
