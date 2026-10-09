import type { ProductionMask } from './cardProduction';

const TAU = Math.PI * 2;

/** Paint real factory ink instead of knocking out each layer's rectangular box.
 * Keep DOM paint order: a later illustration can cover earlier protected ink.
 * Coordinates are converted back from the editor/canvas zoom to authored units. */
export function paintFactoryInkMask(host: HTMLElement, layer: HTMLElement, context: CanvasRenderingContext2D, selection?: ProductionMask) {
  const drawing = selection ? host.querySelector<HTMLElement>('[data-ink-source]') ?? host : host;
  const kind = (element: HTMLElement) => element.dataset.kind ?? element.dataset.inkKind;
  const bounds = layer.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const sx = layer.offsetWidth / bounds.width, sy = layer.offsetHeight / bounds.height;
  const box = (rect: DOMRect) => ({ x: (rect.left - bounds.left) * sx, y: (rect.top - bounds.top) * sy, width: rect.width * sx, height: rect.height * sy });
  const backed = (element: HTMLElement, colour: string) => {
    const rect = box(element.getBoundingClientRect()), style = getComputedStyle(element);
    context.beginPath(); context.roundRect(rect.x, rect.y, rect.width, rect.height, parseFloat(style.borderTopLeftRadius) || 0);
    context.fillStyle = colour; context.fill();
  };
  const text = (element: HTMLElement) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || parent.closest(selection ? 'svg,input,textarea,select' : 'svg,button,input,textarea,select')) continue;
      const style = getComputedStyle(parent);
      if (!selection && style.opacity === '0') continue;
      context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      context.textAlign = 'left'; context.textBaseline = 'alphabetic';
      context.direction = style.direction === 'rtl' ? 'rtl' : 'ltr';
      context.fillStyle = '#fff'; context.strokeStyle = '#fff'; context.lineWidth = .7;
      let offset = 0;
      for (const glyph of node.textContent ?? '') {
        const start = offset; offset += glyph.length;
        if (!glyph.trim()) continue;
        range.setStart(node, start); range.setEnd(node, offset);
        const rect = box(range.getBoundingClientRect());
        if (!rect.width || !rect.height) continue;
        const metrics = context.measureText(glyph), size = parseFloat(style.fontSize);
        const ascent = metrics.fontBoundingBoxAscent ?? size * .8, descent = metrics.fontBoundingBoxDescent ?? size * .2;
        const baseline = rect.y + ascent + (rect.height - ascent - descent) / 2;
        const x = style.direction === 'rtl' ? rect.x + rect.width : rect.x;
        if (!selection) context.strokeText(glyph, x, baseline);
        context.fillText(glyph, x, baseline);
      }
    }
    range.detach();
  };
  const icons = (element: Element) => {
    if (typeof Path2D === 'undefined') return;
    const selector = 'path,circle,ellipse,rect,line,polyline,polygon';
    const geometries = element.matches(selector) ? [element as SVGGeometryElement] : [...element.querySelectorAll<SVGGeometryElement>(selector)];
    geometries.forEach(geometry => {
      const matrix = geometry.getScreenCTM();
      if (!matrix) return;
      const style = getComputedStyle(geometry), number = (name: string) => Number(geometry.getAttribute(name) ?? 0);
      const path = new Path2D();
      switch (geometry.tagName.toLowerCase()) {
        case 'path': path.addPath(new Path2D(geometry.getAttribute('d') ?? '')); break;
        case 'circle': path.arc(number('cx'), number('cy'), number('r'), 0, TAU); break;
        case 'ellipse': path.ellipse(number('cx'), number('cy'), number('rx'), number('ry'), 0, 0, TAU); break;
        case 'rect': path.roundRect(number('x'), number('y'), number('width'), number('height'), number('rx')); break;
        case 'line': path.moveTo(number('x1'), number('y1')); path.lineTo(number('x2'), number('y2')); break;
        default: {
          const points = (geometry.getAttribute('points') ?? '').trim().split(/[\s,]+/).map(Number);
          for (let i = 0; i + 1 < points.length; i += 2) if (i) path.lineTo(points[i], points[i + 1]); else path.moveTo(points[i], points[i + 1]);
          if (geometry.tagName.toLowerCase() === 'polygon') path.closePath();
        }
      }
      context.save();
      context.transform(matrix.a * sx, matrix.b * sy, matrix.c * sx, matrix.d * sy, (matrix.e - bounds.left) * sx, (matrix.f - bounds.top) * sy);
      context.fillStyle = '#fff'; context.strokeStyle = '#fff';
      context.lineCap = style.strokeLinecap as CanvasLineCap; context.lineJoin = style.strokeLinejoin as CanvasLineJoin;
      context.lineWidth = (parseFloat(style.strokeWidth) || 0) + (selection ? 0 : .7 / Math.max(.01, Math.hypot(matrix.a * sx, matrix.b * sy)));
      if (style.fill !== 'none' && style.fillOpacity !== '0') context.fill(path, style.fillRule === 'evenodd' ? 'evenodd' : 'nonzero');
      if (style.stroke !== 'none' && style.strokeOpacity !== '0') context.stroke(path);
      context.restore();
    });
  };
  const selected = (element: HTMLElement) => !selection || (selection.source === 'elements'
    ? selection.elementIds.includes(element.dataset.faceElement ?? element.dataset.inkElement ?? '')
    : selection.source === 'artwork' ? kind(element) === 'illustration'
    : selection.source === 'shapes' ? kind(element) === 'icon'
    : selection.source === 'text' ? kind(element) !== 'illustration' && kind(element) !== 'icon' : false);
  drawing.querySelectorAll<HTMLElement>(selection ? '[data-face-element],[data-ink-element]' : '[data-face-element]').forEach(element => {
    if (element.closest('.card-finish-surface') !== host || !element.offsetWidth || !element.offsetHeight) return;
    if (!selected(element)) return;
    const style = getComputedStyle(element);
    if (!selection && style.opacity === '0') return;
    context.save();
    const rect = box(element.getBoundingClientRect());
    context.beginPath(); context.rect(rect.x, rect.y, rect.width, rect.height); context.clip();
    if (kind(element) === 'illustration') {
      const illustration = element.querySelector<HTMLElement>('img,.factory-illustration-placeholder');
      if (illustration instanceof HTMLImageElement) {
        // PNG transparency and rounded corners must leave underlying ink intact.
        if (illustration.complete && illustration.naturalWidth) {
          const image = box(illustration.getBoundingClientRect());
          const scale = Math.max(image.width / illustration.naturalWidth, image.height / illustration.naturalHeight);
          const width = illustration.naturalWidth * scale, height = illustration.naturalHeight * scale;
          context.beginPath(); context.roundRect(image.x, image.y, image.width, image.height, parseFloat(getComputedStyle(illustration).borderTopLeftRadius) || 0); context.clip();
          context.globalAlpha = selection ? 1 : Number(style.opacity); context.globalCompositeOperation = selection ? 'source-over' : 'destination-out';
          context.drawImage(illustration, image.x + (image.width - width) / 2, image.y + (image.height - height) / 2, width, height);
          // Keep the RGB protection texture opaque after alpha-aware subtraction.
          if (!selection) {
            context.globalAlpha = 1; context.globalCompositeOperation = 'destination-over';
            context.fillStyle = '#000'; context.fillRect(image.x, image.y, image.width, image.height);
          }
        }
      } else if (illustration) { context.globalAlpha = selection ? 1 : Number(style.opacity); backed(illustration, selection ? '#fff' : '#000'); }
    } else {
      text(element);
      if (selection?.source !== 'text') icons(element);
      if (selection?.source !== 'text') element.querySelectorAll<HTMLElement>('button,input,textarea,select,[contenteditable=true],.factory-face-result').forEach(control => backed(control, '#fff'));
    }
    context.restore();
  });
  if (selection?.source === 'shapes' || selection?.source === 'elements') {
    drawing.querySelectorAll<SVGGeometryElement>('.factory-shapes [data-face-shape],.factory-ink-source-shapes [data-ink-shape]').forEach(shape => {
      if (selection.source === 'shapes' || selection.elementIds.includes(shape.dataset.faceShape ?? shape.dataset.inkShape ?? '')) icons(shape);
    });
  }
  if (selection?.source === 'artwork') {
    drawing.querySelectorAll<HTMLImageElement>('.factory-background,.factory-ink-source-background').forEach(illustration => {
      if (!illustration.complete || !illustration.naturalWidth) return;
      const rect = box(illustration.getBoundingClientRect()), fit = getComputedStyle(illustration).objectFit;
      const scale = (fit === 'contain' ? Math.min : Math.max)(rect.width / illustration.naturalWidth, rect.height / illustration.naturalHeight);
      const width = fit === 'fill' ? rect.width : illustration.naturalWidth * scale;
      const height = fit === 'fill' ? rect.height : illustration.naturalHeight * scale;
      context.save(); context.beginPath(); context.rect(rect.x, rect.y, rect.width, rect.height); context.clip();
      context.drawImage(illustration, rect.x + (rect.width - width) / 2, rect.y + (rect.height - height) / 2, width, height);
      context.restore();
    });
    drawing.querySelectorAll('.world-card-print').forEach(icons);
  }
}
