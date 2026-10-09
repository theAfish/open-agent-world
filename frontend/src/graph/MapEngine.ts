import Sigma from 'sigma';
import { EdgeCurvedArrowProgram } from '@sigma/edge-curve';
import type { CameraState } from 'sigma/types';
import { createGraph, hash, reconcile, rememberPositions, restorePositions, type ViewGraph } from './model';
import type { Island, LayoutResult, NetworkData, NetworkEdge, Point } from './types';

const alphaColor = (hex: string, alpha: number) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${alpha})`;

type Hooks = {
  select(ids: string[], inspect: boolean): void;
  expand(id: string): void;
  menu(id: string, point: Point): void;
  hover(id: string): void;
  layout(islands: Island[], busy: boolean, error?: string): void;
  history(available: boolean): void;
};
type Drag = { start: Point; previous: Point; node?: string; box: boolean; moved: boolean };

/** Imperative rendering boundary: camera frames never become React state updates. */
export class MapEngine {
  readonly graph: ViewGraph = createGraph();
  readonly sigma: Sigma;
  private positions: Map<string, Point>;
  private data: NetworkData = { nodes: [], edges: [] };
  private loops: NetworkEdge[] = [];
  private islands: Island[] = [];
  private worker?: Worker;
  private frame = 0;
  private revision = 0;
  private disposed = false;
  private initialized = false;
  private ready = false;
  private selected = new Set<string>();
  private pendingFocus?: string[];
  private neighbors = new Set<string>();
  private hover = '';
  private filter = new Set<string>();
  private filtered = false;
  private dark = false;
  private level = 1;
  private aggregation = 0;
  private aggregates = new Map<string, Island>();
  private membership = new Map<string, string>();
  private trail: { camera: CameraState; ids: string[] }[] = [];
  private abort = new AbortController();
  private resize: ResizeObserver;
  private theme: MutationObserver;
  private contours: HTMLCanvasElement;
  private drag?: Drag;
  private pointers = new Map<number, Point>();
  private pinchDistance = 0;
  private selectionBox: HTMLDivElement;
  private hoverFrame = 0;
  private hoverPoint?: Point;

  constructor(private element: HTMLElement, private key: string, private hooks: Hooks) {
    this.positions = restorePositions(key);
    this.sigma = new Sigma(this.graph, element, {
      allowInvalidContainer: true, defaultEdgeType: 'arrow', labelFont: 'system-ui, sans-serif',
      edgeProgramClasses: { curved: EdgeCurvedArrowProgram },
      labelSize: 12, labelWeight: '400', labelDensity: .35, labelGridCellSize: 160,
      zoomToSizeRatioFunction: ratio => Math.pow(ratio, .15),
      labelRenderedSizeThreshold: 2.8, renderEdgeLabels: false, zIndex: true,
      minCameraRatio: .025, maxCameraRatio: 12, stagePadding: 50,
      hideEdgesOnMove: true, hideLabelsOnMove: true,
      nodeReducer: (id, attrs) => this.nodeStyle(id, attrs),
      edgeReducer: (id, attrs) => this.edgeStyle(id, attrs),
    });
    // OAW embeds maps under transformed cards. Own pointer coordinates in CSS pixels;
    // Sigma's built-in captors assume an unscaled parent. Rendering remains Sigma's.
    this.sigma.getMouseCaptor().enabled = false;
    this.sigma.getTouchCaptor().enabled = false;
    this.contours = this.sigma.createCanvas('islands', { beforeLayer: 'edges', style: { pointerEvents: 'none' } });
    this.selectionBox = document.createElement('div');
    this.selectionBox.className = 'network-selection-box';
    this.selectionBox.hidden = true;
    element.append(this.selectionBox);
    this.sigma.on('afterRender', this.drawContours);
    this.sigma.getCamera().on('updated', this.cameraChanged);
    this.resize = new ResizeObserver(() => this.sigma.resize());
    this.resize.observe(element);
    this.theme = new MutationObserver(this.readTheme);
    this.theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });
    this.readTheme();
    this.listen();
    // Read-only measurements for performance tooling; no document data or authority.
    Object.defineProperty(element, 'networkDiagnostics', { configurable: true, get: () => ({
      nodes: this.data.nodes.length, edges: this.data.edges.length, camera: this.sigma.getCamera().getState(),
      worker: !!this.worker, ready: this.ready, islands: this.islands.length,
      bbox: this.sigma.getCustomBBox(),
      positions: this.data.nodes.map(n => ({ id: n.id, ...this.point(n.id), ...this.screen(n.id) })),
    }) });
  }

  private point(id: string): Point { const p = this.graph.getNodeAttributes(id); return { x: p.x, y: p.y }; }
  private screen(id: string) { const p = this.sigma.graphToViewport(this.point(id)); return { screenX: p.x, screenY: p.y }; }
  private reducedMotion() { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  private readTheme = () => {
    this.dark = document.documentElement.dataset.theme === 'dark';
    this.sigma.setSetting('labelColor', { color: this.dark ? '#d8ddd9' : '#404943' });
    this.sigma.scheduleRefresh();
  };
  private isVisible(id: string) { return !this.filtered || this.filter.has(id); }
  private isCollapsed(id: string) {
    const aggregate = this.membership.get(id);
    return this.aggregation === 1 && !!aggregate && this.graph.hasNode(aggregate) && !this.neighbors.has(id);
  }
  private nodeStyle(id: string, attrs: Record<string, any>) {
    if (attrs.aggregate) {
      const island = this.aggregates.get(id);
      const visible = this.aggregation > 0 && !!island && island.members.length > 20 && island.members.some(n => this.isVisible(n));
      return { ...attrs, hidden: !visible, forceLabel: false,
        color: alphaColor(this.dark ? '#748c94' : '#748b8c', this.aggregation) };
    }
    const active = this.selected.size > 0;
    const related = this.neighbors.has(id);
    const aggregate = this.membership.get(id);
    const alpha = aggregate && this.graph.hasNode(aggregate) && !related ? 1 - this.aggregation : 1;
    const baseSize = this.data.nodes.length > 3000 ? 1.4 : this.data.nodes.length > 1000 ? 2.2 : 3.2;
    return { ...attrs, hidden: !this.ready || !this.isVisible(id) || this.isCollapsed(id),
      color: alphaColor(active && !related ? (this.dark ? '#414849' : '#c9d0ce') : attrs.color, alpha),
      size: this.selected.has(id) ? 6 : baseSize + Math.min(.8, Math.log2(this.graph.degree(id) + 1) * .15),
      highlighted: false, zIndex: this.selected.has(id) ? 2 : related ? 1 : 0,
      forceLabel: this.selected.has(id),
      label: active && !related || (alpha < .5 && !related) ? null : String(attrs.label).length > 26 ? `${String(attrs.label).slice(0, 25)}…` : attrs.label,
    };
  }
  private edgeStyle(id: string, attrs: Record<string, any>) {
    const source = this.graph.source(id), target = this.graph.target(id);
    if (attrs.aggregate) return { ...attrs, hidden: this.aggregation === 0 || this.filtered,
      color: alphaColor(this.dark ? '#424e52' : '#d1d9d8', this.aggregation) };
    const active = this.selected.has(source) || this.selected.has(target);
    return { ...attrs, hidden: source === target || !this.ready || !this.isVisible(source) || !this.isVisible(target) || this.isCollapsed(source) || this.isCollapsed(target)
      || (!active && (this.selected.size > 0 || this.level === 0)),
      // At wide scales sample dense background links deterministically. Every
      // selected relation and every aggregate bridge remains available.
      ...(!active && this.data.edges.length > 12000 && this.level !== 2 && hash(id) % 4 !== 0 ? { hidden: true } : {}),
      color: active ? (this.dark ? '#93adaf' : '#7d999e') : alphaColor(this.dark ? '#344144' : '#d7e0df', 1 - this.aggregation),
      size: active ? 1.1 : .45, forceLabel: active && this.level === 2,
    };
  }

  update(data: NetworkData) {
    this.data = data;
    this.loops = data.edges.filter(edge => edge.source === edge.target);
    const density = this.level === 2 ? .65 : data.nodes.length > 300 ? .2 : .5;
    if (this.sigma.getSetting('labelDensity') !== density) this.sigma.setSetting('labelDensity', density);
    const diff = reconcile(this.graph, data, this.positions);
    if (diff.topology) {
      const pairs = new Map<string, string[]>();
      for (const edge of data.edges) if (this.graph.hasEdge(edge.id)) {
        const key = JSON.stringify([edge.source, edge.target].sort());
        const ids = pairs.get(key) ?? []; ids.push(edge.id); pairs.set(key, ids);
      }
      for (const ids of pairs.values()) ids.sort().forEach((id, i) => this.graph.mergeEdgeAttributes(id,
        { type: 'curved', curvature: ids.length === 1 ? .08 : .15 + i * .18 }));
    }
    this.updateNeighbors();
    if (diff.topology || diff.groups || (!this.initialized && !this.worker)) this.layout(false);
  }
  setSelection(ids: string[], focus = false) {
    const next = ids.filter(id => this.graph.hasNode(id) && !this.aggregates.has(id));
    if (next.length === this.selected.size && next.every(id => this.selected.has(id))) return;
    this.pendingFocus = undefined;
    if (focus && next.length) {
      if (!this.ready || this.worker) { this.rememberFocus(); this.pendingFocus = next; }
      else this.focus(this.focusNeighborhood(next));
    }
    this.selected = new Set(next); this.updateNeighbors(); this.sigma.scheduleRefresh();
  }
  setFilter(ids: string[] | null) { this.filtered = ids !== null; this.filter = new Set(ids); this.sigma.scheduleRefresh(); }
  private updateNeighbors() {
    this.neighbors = new Set(this.selected);
    for (const id of [...this.neighbors]) if (this.graph.hasNode(id)) for (const n of this.graph.neighbors(id)) this.neighbors.add(n);
  }
  private stopLayout() {
    this.worker?.terminate(); this.worker = undefined;
    cancelAnimationFrame(this.frame); this.frame = 0;
  }
  arrange() { this.positions.clear(); this.layout(true); }
  private layout(arrange: boolean) {
    this.stopLayout();
    const revision = ++this.revision;
    if (!this.data.nodes.length) {
      this.islands = []; this.clearAggregates(); this.ready = true;
      this.hooks.layout([], false); this.sigma.scheduleRefresh(); return;
    }
    this.hooks.layout(this.islands, true);
    const fail = (message: string) => {
      if (this.disposed || revision !== this.revision) return;
      this.stopLayout(); this.ready = true; this.sigma.scheduleRefresh();
      this.hooks.layout(this.islands, false, message);
    };
    try {
      const worker = this.worker = new Worker(new URL('./layout.worker.ts', import.meta.url), { type: 'module' });
      worker.onerror = event => fail(event.message || 'Layout worker failed');
      worker.onmessage = (event: MessageEvent<{ result?: LayoutResult; error?: string }>) => {
        if (this.disposed || revision !== this.revision) return;
        if (!event.data.result) { fail(event.data.error ?? 'Layout failed'); return; }
        worker.terminate(); this.worker = undefined;
        const result = event.data.result;
        const initial = !this.initialized;
        const starts = new Map(result.positions.map(([id]) => [id, this.point(id)]));
        const started = performance.now(), duration = initial || this.reducedMotion() ? 0 : 260;
        const apply = () => {
          if (this.disposed || revision !== this.revision) return;
          const progress = duration ? Math.min(1, (performance.now() - started) / duration) : 1;
          const eased = 1 - (1 - progress) ** 3;
          const targets = new Map(result.positions);
          this.graph.updateEachNodeAttributes((id, attrs) => {
            const end = targets.get(id), start = starts.get(id);
            return end && start ? { ...attrs, x: start.x + (end.x - start.x) * eased, y: start.y + (end.y - start.y) * eased } : attrs;
          }, { attributes: ['x', 'y'] });
          if (progress < 1) { this.frame = requestAnimationFrame(apply); return; }
          this.frame = 0;
          for (const [id, point] of result.positions) this.positions.set(id, point);
          // Keep filtered-out nodes cached, but cap per-map growth in long sessions.
          while (this.positions.size > 12000) this.positions.delete(this.positions.keys().next().value!);
          this.islands = result.islands; this.rebuildAggregates();
          this.ready = true;
          if (initial || arrange) {
            this.sigma.setCustomBBox(null); this.sigma.refresh();
            const bbox = this.sigma.getBBox();
            // Freeze normalization; appending nodes must not move the camera's coordinate system.
            this.sigma.setCustomBBox(bbox); this.sigma.refresh(); this.fit();
          }
          this.initialized = true;
          if (this.pendingFocus) {
            this.focus(this.focusNeighborhood(this.pendingFocus), false); this.pendingFocus = undefined;
          }
          this.sigma.scheduleRefresh();
          this.element.dataset.layoutMs = result.duration.toFixed(1);
          this.hooks.layout(this.islands, false);
        };
        apply();
      };
      worker.postMessage({ ...this.data, positions: arrange ? [] : [...this.positions],
        communities: arrange ? [] : this.islands.flatMap(i => i.members.map(id => [id, i.id])) });
    } catch (error) { fail(String(error)); }
  }
  private clearAggregates() {
    this.graph.forEachEdge((id, attrs) => { if (attrs.aggregate) this.graph.dropEdge(id); });
    for (const id of this.aggregates.keys()) if (this.graph.hasNode(id)) this.graph.dropNode(id);
    this.aggregates.clear(); this.membership.clear();
  }
  private rebuildAggregates() {
    this.clearAggregates();
    const liveIds = new Set(this.data.nodes.map(n => n.id));
    const candidates = this.islands.filter(island => island.members.length > 20).map(island => ({ island,
      radius: Math.sqrt(island.members.reduce((sum, id) => {
        const point = this.point(id); return sum + (point.x - island.center.x) ** 2 + (point.y - island.center.y) ** 2;
      }, 0) / island.members.length),
    }));
    // Interwoven topics may share the same center in a natural layout. Keep their
    // stars visible instead of collapsing several communities onto one marker.
    const separable = new Set(candidates.filter(a => !candidates.some(b => a !== b
      && Math.hypot(a.island.center.x - b.island.center.x, a.island.center.y - b.island.center.y) < (a.radius + b.radius) * .5))
      .map(candidate => candidate.island.id));
    for (const [index, island] of this.islands.entries()) {
      let id = `\u0000island:${index}`;
      while (liveIds.has(id) || this.graph.hasNode(id)) id += ':';
      this.aggregates.set(id, island);
      island.members.forEach(member => this.membership.set(member, id));
      if (separable.has(island.id)) this.graph.addNode(id, { ...island.center, label: island.label.slice(0, 24), size: 8, color: '#829798', aggregate: true });
    }
    const links = new Map<string, { source: string; target: string; count: number }>();
    for (const edge of this.data.edges) {
      const a = this.membership.get(edge.source), b = this.membership.get(edge.target);
      if (!a || !b || a === b) continue;
      const source = this.graph.hasNode(a) ? a : edge.source;
      const target = this.graph.hasNode(b) ? b : edge.target;
      if (source === edge.source && target === edge.target) continue;
      const key = JSON.stringify([source, target]);
      const prev = links.get(key);
      links.set(key, { source, target, count: (prev?.count ?? 0) + 1 });
    }
    for (const link of links.values()) this.graph.addDirectedEdge(link.source, link.target,
      { aggregate: true, size: Math.min(2, .5 + Math.log2(link.count + 1) * .2), color: '#879b9b', type: 'line', label: `${link.count}` });
  }

  private cameraChanged = () => {
    const ratio = this.sigma.getCamera().ratio;
    // Eight opacity steps crossfade stars and their aggregate without rebuilding
    // graph topology or making React track camera frames.
    const aggregation = Math.round(Math.max(0, Math.min(1, (ratio - 1.6) / .6)) * 8) / 8;
    if (aggregation !== this.aggregation) { this.aggregation = aggregation; this.sigma.scheduleRefresh(); }
    const next = ratio > (this.level === 0 ? 1.8 : 2.1) ? 0 : ratio < (this.level === 2 ? .4 : .32) ? 2 : 1;
    if (next !== this.level) {
      this.level = next;
      this.sigma.setSettings({ labelDensity: next === 2 ? .65 : this.data.nodes.length > 300 ? .2 : .5, labelRenderedSizeThreshold: next === 2 ? 2 : 2.8, renderEdgeLabels: next === 2 });
    }
  };
  private drawContours = () => {
    const { width, height } = this.sigma.getDimensions(), dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (this.contours.width !== Math.round(width * dpr) || this.contours.height !== Math.round(height * dpr)) {
      this.contours.width = Math.round(width * dpr); this.contours.height = Math.round(height * dpr);
      this.contours.style.width = `${width}px`; this.contours.style.height = `${height}px`;
    }
    const ctx = this.contours.getContext('2d'); if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
    if (!this.ready) return;
    const origin = this.sigma.graphToViewport({ x: 0, y: 0 }), unit = this.sigma.graphToViewport({ x: 1, y: 1 });
    const sx = unit.x - origin.x, sy = unit.y - origin.y;
    const alpha = Math.min(1, this.sigma.getCamera().ratio * 3);
    const labels: { x: number; y: number; width: number }[] = [];
    ctx.lineWidth = 1;
    for (const island of this.islands) {
      if (island.members.length < 2 || (this.filtered && !island.members.some(id => this.filter.has(id)))) continue;
      const active = this.selected.size === 0 || island.members.some(id => this.selected.has(id));
      ctx.globalAlpha = alpha * (active ? 1 : .35);
      ctx.fillStyle = this.dark ? 'rgba(109,156,160,.075)' : 'rgba(114,161,162,.065)';
      ctx.strokeStyle = this.dark ? 'rgba(135,171,171,.13)' : 'rgba(106,149,150,.18)';
      ctx.beginPath();
      for (const polygon of island.rings) for (const ring of polygon) {
        ring.forEach(([x, y], i) => { const px = origin.x + x * sx, py = origin.y + y * sy; if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py); });
        ctx.closePath();
      }
      ctx.fill('evenodd'); ctx.stroke();
      if (this.level === 1 && island.members.length > 3) {
        const p = this.sigma.graphToViewport(island.labelPosition);
        ctx.fillStyle = this.dark ? '#a3b4b2' : '#687d7c'; ctx.font = '600 11px system-ui';
        const text = `${island.label.slice(0, 32)} · ${island.members.length}`, width = ctx.measureText(text).width;
        if (!labels.some(label => Math.abs(label.y - p.y) < 18 && Math.abs(label.x - p.x) < (label.width + width) / 2 + 12)) {
          ctx.textAlign = 'center'; ctx.fillText(text, p.x, p.y - 25); ctx.textAlign = 'start';
          labels.push({ x: p.x, y: p.y, width });
        }
      }
    }
    ctx.globalAlpha = 1;
    // Sigma 3 curves have no self-loop primitive. The few self-relations are a
    // light overlay in the same camera, retaining their labels and direction.
    for (const edge of this.loops) if (this.graph.hasNode(edge.source) && this.isVisible(edge.source) && !this.isCollapsed(edge.source)) {
      const p = this.sigma.graphToViewport(this.point(edge.source));
      ctx.strokeStyle = this.dark ? '#738e92' : '#93aaad'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x + 7, p.y - 7, 10, .6, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(p.x + 14, p.y - 12); ctx.lineTo(p.x + 17, p.y - 7); ctx.lineTo(p.x + 20, p.y - 12); ctx.stroke();
      if (this.selected.has(edge.source)) {
        ctx.fillStyle = this.dark ? '#d8ddd9' : '#404943'; ctx.font = '11px system-ui'; ctx.fillText(edge.label ?? '', p.x + 18, p.y - 12);
      }
    }
  };

  fit() { this.focus(this.data.nodes.filter(n => this.isVisible(n.id)).map(n => n.id), false); }
  private rememberFocus() {
    this.trail.push({ camera: this.sigma.getCamera().getState(), ids: [...this.selected] });
    this.trail = this.trail.slice(-20); this.hooks.history(true);
  }
  private focusNeighborhood(ids: string[]) {
    if (ids.length !== 1) return ids;
    const origin = this.point(ids[0]);
    const distance = (id: string) => { const p = this.point(id); return Math.hypot(p.x - origin.x, p.y - origin.y); };
    // Keep nearby relationships in the frame; a remote bridge or a large hub
    // must not force every click back to a view of the entire graph.
    const nearby = this.graph.neighbors(ids[0]).filter(id => !this.aggregates.has(id) && this.isVisible(id))
      .sort((a, b) => distance(a) - distance(b)).slice(0, 8);
    return [...ids, ...nearby];
  }
  focus(ids: string[], save = true) {
    const points = ids.filter(id => this.graph.hasNode(id)).map(id => this.point(id));
    if (!points.length) return;
    if (save) this.rememberFocus();
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const p of points) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y); }
    const { width, height } = this.sigma.getDimensions();
    const center = this.sigma.viewportToFramedGraph(this.sigma.graphToViewport({ x: (xmin + xmax) / 2, y: (ymin + ymax) / 2 }));
    const override = { cameraState: { x: .5, y: .5, angle: 0, ratio: 1 } };
    const a = this.sigma.graphToViewport({ x: xmin, y: ymin }, override), b = this.sigma.graphToViewport({ x: xmax, y: ymax }, override);
    const ratio = points.length === 1 ? .2 : Math.max(Math.abs(b.x - a.x) / Math.max(60, width - 160), Math.abs(b.y - a.y) / Math.max(60, height - 160));
    void this.sigma.getCamera().animate({ ...center, ratio: Math.max(.08, Math.min(12, ratio || 1)) }, { duration: this.reducedMotion() ? 0 : 280 })
      .then(() => { if (!this.disposed) this.sigma.scheduleRender(); });
  }
  back() {
    const previous = this.trail.pop(); if (!previous) return;
    this.setSelection(previous.ids); this.hooks.select(previous.ids, !!previous.ids.length);
    void this.sigma.getCamera().animate(previous.camera, { duration: this.reducedMotion() ? 0 : 260 })
      .then(() => { if (!this.disposed) this.sigma.scheduleRender(); });
    this.hooks.history(this.trail.length > 0);
  }
  choose(id: string) {
    const island = this.aggregates.get(id);
    if (island) { this.focus(island.members); return; }
    this.focus(this.focusNeighborhood([id])); this.setSelection([id]); this.hooks.select([id], true);
  }
  zoom(factor: number, point?: Point) {
    const camera = this.sigma.getCamera(), { width, height } = this.sigma.getDimensions();
    const ratio = Math.max(.025, Math.min(12, camera.ratio * factor));
    camera.setState(this.sigma.getViewportZoomedState(point ?? { x: width / 2, y: height / 2 }, ratio));
  }
  private local(event: { clientX: number; clientY: number }): Point {
    const rect = this.element.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * this.element.clientWidth / rect.width, y: (event.clientY - rect.top) * this.element.clientHeight / rect.height };
  }
  private hit(point: Point) {
    if (!this.ready) return '';
    // Convert the pointer once instead of rebuilding Sigma's projection matrix
    // for every node on every hover frame. Distance is measured in graph space.
    const origin = this.sigma.viewportToGraph(point);
    const boundary = this.sigma.viewportToGraph({ x: point.x + 12, y: point.y });
    let found = '', closest = (boundary.x - origin.x) ** 2 + (boundary.y - origin.y) ** 2;
    this.graph.forEachNode((id, attrs) => {
      const distance = (attrs.x - origin.x) ** 2 + (attrs.y - origin.y) ** 2;
      if (distance >= closest) return;
      if (attrs.aggregate ? !this.aggregation || !this.aggregates.get(id)!.members.some(n => this.isVisible(n))
        : !this.isVisible(id) || this.isCollapsed(id)) return;
      closest = distance; found = id;
    });
    return found;
  }
  private pan(previous: Point, current: Point) {
    const a = this.sigma.viewportToFramedGraph(previous), b = this.sigma.viewportToFramedGraph(current), camera = this.sigma.getCamera();
    camera.setState({ x: camera.x + a.x - b.x, y: camera.y + a.y - b.y });
  }
  private listen() {
    const options = { signal: this.abort.signal };
    this.element.addEventListener('pointerdown', event => {
      if (event.button !== 0 && event.button !== 1) return;
      event.preventDefault(); this.element.focus({ preventScroll: true }); this.element.setPointerCapture(event.pointerId);
      const point = this.local(event); this.pointers.set(event.pointerId, point);
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()]; this.pinchDistance = Math.hypot(a.x - b.x, a.y - b.y); this.drag = undefined; return;
      }
      this.drag = { start: point, previous: point, box: event.shiftKey, node: event.button === 0 && !event.shiftKey ? this.hit(point) : undefined, moved: false };
    }, options);
    this.element.addEventListener('pointermove', event => {
      const point = this.local(event);
      if (this.pointers.has(event.pointerId)) this.pointers.set(event.pointerId, point);
      if (this.pointers.size >= 2) {
        const [a, b] = [...this.pointers.values()], distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (this.pinchDistance > 0 && distance > 0) this.zoom(this.pinchDistance / distance, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
        this.pinchDistance = distance; return;
      }
      const drag = this.drag;
      if (drag) {
        if (Math.hypot(point.x - drag.start.x, point.y - drag.start.y) > 3) drag.moved = true;
        if (!drag.moved) return;
        if (drag.box) {
          this.selectionBox.hidden = false;
          Object.assign(this.selectionBox.style, { left: `${Math.min(drag.start.x, point.x)}px`, top: `${Math.min(drag.start.y, point.y)}px`, width: `${Math.abs(point.x - drag.start.x)}px`, height: `${Math.abs(point.y - drag.start.y)}px` });
        } else if (drag.node && !this.aggregates.has(drag.node)) {
          this.stopLayout();
          const p = this.sigma.viewportToGraph(point); this.graph.mergeNodeAttributes(drag.node, p); this.positions.set(drag.node, p);
        } else this.pan(drag.previous, point);
        drag.previous = point; return;
      }
      this.hoverPoint = point;
      if (!this.hoverFrame) this.hoverFrame = requestAnimationFrame(() => {
        this.hoverFrame = 0; const id = this.hit(this.hoverPoint!);
        this.element.style.cursor = id ? 'pointer' : 'grab';
        if (id === this.hover) return;
        // Hover is a caption/cursor interaction. Only selection changes graph
        // emphasis, avoiding a full GPU buffer upload for each crossed node.
        this.hover = id; this.hooks.hover(this.aggregates.has(id) ? '' : id);
      });
    }, options);
    const finish = (event: PointerEvent) => {
      this.pointers.delete(event.pointerId);
      if (this.element.hasPointerCapture(event.pointerId)) this.element.releasePointerCapture(event.pointerId);
      const drag = this.drag; this.drag = undefined; this.selectionBox.hidden = true;
      if (!drag || event.type === 'pointercancel') return;
      if (drag.box && drag.moved) {
        const ids: string[] = [], end = this.local(event);
        this.graph.forEachNode((id, attrs) => {
          if (attrs.aggregate || !this.isVisible(id) || this.isCollapsed(id)) return;
          const p = this.sigma.graphToViewport(attrs as Point);
          if (p.x >= Math.min(drag.start.x, end.x) && p.x <= Math.max(drag.start.x, end.x) && p.y >= Math.min(drag.start.y, end.y) && p.y <= Math.max(drag.start.y, end.y)) ids.push(id);
        });
        this.setSelection(ids); this.hooks.select(ids, false);
      } else if (!drag.moved && event.button === 0) {
        if (drag.node) this.choose(drag.node);
        else { this.setSelection([]); this.hooks.select([], false); }
      } else if (drag.node && !this.aggregates.has(drag.node)) this.layout(false);
    };
    this.element.addEventListener('pointerup', finish, options);
    this.element.addEventListener('pointercancel', finish, options);
    this.element.addEventListener('pointerleave', () => {
      if (!this.drag) { this.hover = ''; this.hooks.hover(''); }
    }, options);
    this.element.addEventListener('wheel', event => { event.preventDefault(); this.zoom(Math.exp(Math.max(-.3, Math.min(.3, event.deltaY * .002))), this.local(event)); }, { ...options, passive: false });
    this.element.addEventListener('dblclick', event => { event.preventDefault(); const id = this.hit(this.local(event)); if (id && !this.aggregates.has(id)) this.hooks.expand(id); }, options);
    this.element.addEventListener('contextmenu', event => { event.preventDefault(); const id = this.hit(this.local(event)); if (id && !this.aggregates.has(id)) this.hooks.menu(id, { x: event.clientX, y: event.clientY }); }, options);
    this.element.addEventListener('keydown', event => {
      if (!['+', '=', '-', 'Home', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === 'Home') this.fit();
      else if (event.key === 'Escape') { this.setSelection([]); this.hooks.select([], false); }
      else if (['+', '=', '-'].includes(event.key)) this.zoom(event.key === '-' ? 1.25 : .8);
      else this.pan({ x: 0, y: 0 }, { x: event.key === 'ArrowLeft' ? 40 : event.key === 'ArrowRight' ? -40 : 0, y: event.key === 'ArrowUp' ? 40 : event.key === 'ArrowDown' ? -40 : 0 });
    }, options);
  }
  destroy() {
    this.disposed = true; this.revision++; this.stopLayout(); cancelAnimationFrame(this.hoverFrame);
    this.abort.abort(); this.resize.disconnect(); this.theme.disconnect();
    rememberPositions(this.key, this.positions);
    this.sigma.off('afterRender', this.drawContours); this.sigma.getCamera().off('updated', this.cameraChanged);
    this.sigma.kill(); this.graph.clear(); this.selectionBox.remove();
    delete (this.element as HTMLElement & { networkDiagnostics?: unknown }).networkDiagnostics;
  }
}
