/** Bounded, model-derived thumbnails. Never capture or mount a live workspace.
 * SVG is used as an image resource: its panels/text aren't page DOM or hit targets. */
export interface StaticWorkspaceModel {
  runtime: string; path: string; filename: string; fileText: string; output: string; command: string;
  status: string; settings: boolean; sidebar: number; terminal: number; readOnly: boolean; network: boolean;
  labels: Record<'workspace' | 'settings' | 'files' | 'preview' | 'terminal' | 'history' | 'empty' | 'output' | 'runtime' | 'access' | 'network' | 'enabled' | 'disabled' | 'readOnly' | 'readWrite', string>;
}
const images = new Map<string, string>();
const palettes = new Map<string, Record<string, string>>();
const escape = (text: string) => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c]!);
function palette(theme: string) {
  const known = palettes.get(theme); if (known) return known;
  const style = getComputedStyle(document.documentElement);
  const fallback = theme === 'dark' ? ['#383732', '#32312c', '#c2bdb1', '#938d82', '#57554c', '#d07a61']
    : ['#fffefa', '#f7f6f1', '#625e55', '#888277', '#ddd9d1', '#b45f48'];
  const result = Object.fromEntries(['card-surface', 'card-soft', 'ink-soft', 'ink-faint', 'line', 'accent']
    .map((name, i) => [name, style.getPropertyValue(`--${name}`).trim() || fallback[i]]));
  palettes.set(theme, result); return result;
}
export function staticWorkspaceImage(model: StaticWorkspaceModel, theme: string, width = 1020, height = 644) {
  const w = Math.max(320, width), h = Math.max(200, height);
  const key = JSON.stringify([model, theme, w, h]);
  const known = images.get(key);
  if (known) { images.delete(key); images.set(key, known); return known; }
  const p = palette(theme), l = model.labels;
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(w * 320 / Math.max(w, h))}" height="${Math.round(h * 320 / Math.max(w, h))}" viewBox="0 0 ${w} ${h}">`];
  const rect = (x: number, y: number, width: number, height: number, color: string) => parts.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="${color}"/>`);
  const text = (value: string, x: number, y: number, max: number, color = p['ink-soft'], size = 13) => {
    const limit = Math.max(1, Math.floor(max / (size * .62)));
    const valueBounded = value.length > limit ? `${value.slice(0, Math.max(1, limit - 1))}…` : value;
    parts.push(`<text x="${x}" y="${y}" fill="${color}" font-family="Segoe UI, sans-serif" font-size="${size}">${escape(valueBounded)}</text>`);
  };
  const lines = (value: string, x: number, y: number, max: number, availableHeight: number) => {
    const count = Math.min(18, Math.floor(availableHeight / 20));
    value.split('\n').slice(0, count).forEach((line, i) => text(line, x, y + i * 20, max, p['ink-soft'], 12));
  };
  rect(0, 0, w, h, p['card-surface']); rect(0, 0, w, 42, p['card-soft']);
  text(l.workspace, 16, 26, 110, model.settings ? p['ink-faint'] : p.accent);
  text(l.settings, 145, 26, 100, model.settings ? p.accent : p['ink-faint']);
  text(`${model.runtime} · ${model.status}`, Math.max(270, w - 210), 26, 190, p['ink-faint'], 11);
  rect(model.settings ? 145 : 16, 40, 90, 2, p.accent);
  if (model.settings) {
    const entries = [[l.runtime, model.runtime], [l.workspace, model.path], [l.access, model.readOnly ? l.readOnly : l.readWrite], [l.network, model.network ? l.enabled : l.disabled]];
    entries.forEach(([label, value], i) => {
      const x = 28 + i % 2 * (w / 2), y = 80 + Math.floor(i / 2) * 94;
      text(label, x, y, w / 2 - 50, p['ink-faint']);
      rect(x, y + 12, w / 2 - 50, 38, p['card-soft']); text(value, x + 10, y + 36, w / 2 - 70);
    });
  } else {
    const sidebar = w * model.sidebar / 100, terminalTop = 42 + (h - 42) * (1 - model.terminal / 100);
    rect(0, 42, sidebar, h - 42, p['card-soft']); rect(sidebar, 42, 3, h - 42, p.line);
    rect(sidebar + 3, 42, w - sidebar - 3, 36, p['card-soft']);
    rect(sidebar + 3, terminalTop, w - sidebar - 3, h - terminalTop, p['card-soft']);
    rect(sidebar, terminalTop, w - sidebar, 3, p.line);
    rect(0, 78, w, 1, p.line); rect(sidebar, terminalTop + 36, w - sidebar, 1, p.line);
    text(l.files, 16, 65, sidebar - 30); text(model.path, 16, 106, sidebar - 30);
    if (model.filename) text(model.filename, 28, 138, sidebar - 40, p.accent);
    text(model.filename || l.preview, sidebar + 18, 65, w - sidebar - 32);
    if (model.fileText) lines(model.fileText, sidebar + 20, 107, w - sidebar - 40, terminalTop - 114);
    else {
      const cx = (w + sidebar) / 2, cy = (78 + terminalTop) / 2;
      parts.push(`<path d="M${cx - 9} ${cy - 25}h12l7 7v18h-19z M${cx + 3} ${cy - 25}v8h7" stroke="${p['ink-faint']}" fill="none" stroke-width="1.5"/>`);
      text(model.filename || l.empty, cx - 95, cy + 24, 190, p['ink-faint'], 11);
    }
    text(`${l.terminal}     ${l.history}`, sidebar + 18, terminalTop + 25, w - sidebar - 36);
    lines(model.output || l.output, sidebar + 20, terminalTop + 61, w - sidebar - 40, h - terminalTop - 96);
    text(`$ ${model.command}`, sidebar + 20, h - 16, w - sidebar - 40, p.accent, 12);
  }
  parts.push('</svg>');
  const image = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(parts.join(''))}`;
  images.set(key, image);
  if (images.size > 128) images.delete(images.keys().next().value!);
  return image;
}
