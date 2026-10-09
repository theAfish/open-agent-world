import { ACESFilmicToneMapping, Box3, DirectionalLight, Mesh, MeshBasicMaterial, MeshStandardMaterial, OrthographicCamera,
  PCFShadowMap, PMREMGenerator, Scene, SRGBColorSpace, WebGLRenderer, type WebGLRenderTarget } from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { createMicrostructure } from './materials';
import { createPackModel } from './model';
import { createContactShadow } from './contactShadow';
import { hoverAngles } from './motion';
import { packModelKey, packPreviewKey, packViewKey, rememberPackPreview } from './preview';
import type { PackRenderHandle, PackRenderOptions } from './types';

type Model = ReturnType<typeof createPackModel>;
interface Slot {
  canvas: HTMLCanvasElement; context: CanvasRenderingContext2D; options: PackRenderOptions;
  model?: Model; key: string; viewKey: string; visible: boolean; dirty: boolean; ready: boolean; opening: number;
  yaw: number; pitch: number; hoverX: number; hoverY: number; tiltX: number; tiltY: number; drawnAt: number;
  onReady(ready: boolean): void;
}
const BUFFER = 1024;
const degrees = Math.PI / 180;
const defaultYaw = (o: PackRenderOptions) => o.view.yaw ?? (o.packaging === 'collector' ? -17 : -7);

/** One GPU context, with immediate copies into ordinary DOM canvases.
 * DOM clipping, dialogs and scroll containers remain native. Idle views do not redraw. */
class PackRenderer {
  readonly renderer = new WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
  readonly scene = new Scene();
  readonly camera = new OrthographicCamera(-2, 2, 2, -2, .1, 40);
  readonly slots = new Set<Slot>();
  readonly micro = createMicrostructure();
  readonly clay = new MeshStandardMaterial({ color: '#b3bbb8', roughness: .68 });
  readonly wire = new MeshBasicMaterial({ color: '#375967', wireframe: true });
  readonly key = new DirectionalLight('#fff3df', 2.0);
  readonly fill = new DirectionalLight('#dceeff', .65);
  readonly contact = createContactShadow();
  readonly bounds = new Box3();
  readonly partBounds = new Box3();
  environment!: WebGLRenderTarget;
  private frame = 0;
  private lost = false;
  private cursor = 0;
  renders = 0;
  constructor() {
    this.renderer.setSize(BUFFER, BUFFER, false); this.renderer.setPixelRatio(1);
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.0;
    this.renderer.setClearColor('#000000', 0);
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = PCFShadowMap;
    this.key.position.set(-3.5, 5.5, 5); this.key.castShadow = true;
    this.key.shadow.mapSize.set(512, 512); this.key.shadow.camera.left = -3; this.key.shadow.camera.right = 3;
    this.key.shadow.camera.top = 4; this.key.shadow.camera.bottom = -3; this.key.shadow.normalBias = .018;
    this.key.shadow.bias = -.00015; this.key.shadow.radius = 2;
    this.fill.position.set(4, 2.5, -2);
    this.scene.add(this.key, this.fill, this.contact.plane);
    this.camera.position.set(0, 1.25, 9); this.camera.lookAt(0, .05, 0);
    this.makeEnvironment();
    this.renderer.domElement.addEventListener('webglcontextlost', this.contextLost);
    this.renderer.domElement.addEventListener('webglcontextrestored', this.contextRestored);
    document.addEventListener('visibilitychange', this.visibility);
  }
  private makeEnvironment() {
    const generator = new PMREMGenerator(this.renderer), room = new RoomEnvironment();
    this.environment?.dispose(); this.environment = generator.fromScene(room, .035);
    this.scene.environment = this.environment.texture; this.scene.environmentIntensity = .45;
    room.dispose(); generator.dispose();
  }
  private contextLost = (event: Event) => {
    event.preventDefault(); this.lost = true; cancelAnimationFrame(this.frame); this.frame = 0;
    this.slots.forEach(slot => { slot.ready = false; slot.onReady(false); });
  };
  private contextRestored = () => { this.lost = false; this.makeEnvironment(); this.slots.forEach(slot => { slot.dirty = true; }); this.invalidate(); };
  private visibility = () => { if (!document.hidden) { this.slots.forEach(slot => { slot.dirty = true; }); this.invalidate(); } };
  invalidate = () => { if (!this.frame && !this.lost && !document.hidden) this.frame = requestAnimationFrame(this.draw); };
  private draw = (now: number) => {
    this.frame = 0; let animate = false, built = false, worked = false;
    const started = performance.now(), slots = [...this.slots], start = this.cursor % Math.max(1, slots.length);
    for (let index = 0; index < slots.length; index++) {
      const position = (start + index) % slots.length, slot = slots[position];
      if (!slot.visible || !slot.dirty) continue;
      const canvas = slot.canvas, width = canvas.clientWidth, height = canvas.clientHeight;
      if (width < 1 || height < 1 || !canvas.isConnected) continue;
      // Yield between expensive views, including on the first frame. Rotate the
      // starting point so an animated pack cannot starve later visible packs.
      if (worked && (performance.now() - started >= 6 || (built && !slot.model))) {
        this.cursor = position; animate = true; break;
      }
      worked = true; this.cursor = (position + 1) % slots.length;
      try {
        if (!slot.model) { slot.model = createPackModel(slot.options, this.micro, () => { slot.dirty = true; this.invalidate(); }); built = true; }
        const scale = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2, BUFFER / width, BUFFER / height);
        const w = Math.max(1, Math.round(width * scale)), h = Math.max(1, Math.round(height * scale));
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
        const o = slot.options;
        const damping = 1 - Math.exp(-Math.min(.05, (now - slot.drawnAt) / 1000) / .05);
        slot.drawnAt = now;
        slot.tiltX += (slot.hoverX - slot.tiltX) * damping;
        slot.tiltY += (slot.hoverY - slot.tiltY) * damping;
        const tilting = Math.abs(slot.hoverX - slot.tiltX) + Math.abs(slot.hoverY - slot.tiltY) > .025;
        if (!tilting) { slot.tiltX = slot.hoverX; slot.tiltY = slot.hoverY; }
        const progress = o.view.opening ?? (o.revealing ? o.reducedMotion ? .6 : Math.min(1, (now - slot.opening) / 2000) : o.opened ? 1 : 0);
        const revealing = o.view.opening !== undefined ? progress > 0 && progress < 1 : o.revealing;
        slot.model.pose(progress, revealing, o.finishVisible || o.view.opening !== undefined);
        const light = (o.view.light ?? 0) * degrees;
        this.scene.environmentRotation.set(0, light, 0);
        this.key.position.set(-3.5 * Math.cos(light) + 4 * Math.sin(light), 5.5, 5 * Math.cos(light) + 3.5 * Math.sin(light));
        const zoom = Math.min(1, progress / .28);
        const spread = revealing ? 2.48 * zoom * zoom * (3 - 2 * zoom) : o.opened && (o.packaging === 'paper' || o.packaging === 'collector') ? .8 : 0;
        slot.model.root.rotation.set((slot.pitch + slot.tiltY) * degrees, (slot.yaw + slot.tiltX) * degrees, 0);
        slot.model.root.updateMatrixWorld(true); this.camera.updateMatrixWorld();
        this.bounds.makeEmpty();
        slot.model.root.traverseVisible(object => {
          if (!(object instanceof Mesh)) return;
          if (!object.geometry.boundingBox) object.geometry.computeBoundingBox();
          this.partBounds.copy(object.geometry.boundingBox!).applyMatrix4(object.matrixWorld).applyMatrix4(this.camera.matrixWorldInverse);
          this.bounds.union(this.partBounds);
        });
        // Include the opened lid and emerging cards from every inspection angle.
        const aspect = width / height, margin = .16;
        const viewHeight = Math.max(4.12 + spread, this.bounds.max.y - this.bounds.min.y + margin * 2,
          (this.bounds.max.x - this.bounds.min.x + margin * 2) / aspect);
        const halfWidth = viewHeight * aspect / 2;
        const centerX = Math.max(this.bounds.max.x - halfWidth + margin, Math.min(0, this.bounds.min.x + halfWidth - margin));
        const centerY = Math.max(this.bounds.max.y - viewHeight / 2 + margin, Math.min(spread * .44, this.bounds.min.y + viewHeight / 2 - margin));
        this.camera.left = centerX - halfWidth; this.camera.right = centerX + halfWidth;
        this.camera.top = viewHeight / 2 + centerY; this.camera.bottom = -viewHeight / 2 + centerY; this.camera.updateProjectionMatrix();
        slot.model.surface(o.view.surface === 'clay' ? this.clay : o.view.surface === 'wireframe' ? this.wire : undefined);
        this.scene.add(slot.model.root);
        this.contact.render(this.renderer, this.scene, this.key.position);
        this.renderer.setViewport(0, 0, w, h); this.renderer.setScissor(0, 0, w, h); this.renderer.setScissorTest(true);
        this.renderer.clear(); this.renderer.render(this.scene, this.camera);
        slot.context.clearRect(0, 0, w, h);
        slot.context.drawImage(this.renderer.domElement, 0, BUFFER - h, w, h, 0, 0, w, h);
        this.scene.remove(slot.model.root); this.renders++;
        slot.dirty = tilting || (o.revealing && !o.reducedMotion && progress < 1);
        canvas.dataset.yaw = slot.yaw.toFixed(1); canvas.dataset.pitch = slot.pitch.toFixed(1);
        canvas.dataset.tiltYaw = slot.tiltX.toFixed(2); canvas.dataset.tiltPitch = slot.tiltY.toFixed(2);
        canvas.dataset.opening = progress.toFixed(3);
        if (!slot.ready) { slot.ready = true; slot.onReady(true); }
        if (!o.revealing && !tilting && !slot.tiltX && !slot.tiltY
          && slot.yaw === defaultYaw(o) && slot.pitch === (o.view.pitch ?? -3)) {
          rememberPackPreview(packPreviewKey(o), canvas);
        }
        if (slot.dirty) animate = true;
      } catch (error) {
        if (slot.model) this.scene.remove(slot.model.root);
        slot.visible = false; slot.onReady(false);
        console.warn('Pack 3D rendering unavailable; using the accessible cover.', error);
      }
    }
    if (animate) this.invalidate();
  };
  attach(canvas: HTMLCanvasElement, options: PackRenderOptions, onReady: Slot['onReady']): PackRenderHandle {
    const context = canvas.getContext('2d', { alpha: true });
    if (!context) throw new Error('Pack canvas unavailable');
    const slot: Slot = { canvas, context, options, key: packModelKey(options), viewKey: packViewKey(options), dirty: true, visible: false, ready: false, opening: performance.now(),
      yaw: defaultYaw(options), pitch: options.view.pitch ?? -3, hoverX: 0, hoverY: 0, tiltX: 0, tiltY: 0, drawnAt: performance.now(), onReady };
    this.slots.add(slot);
    const resize = new ResizeObserver(() => { slot.dirty = true; this.invalidate(); }); resize.observe(canvas);
    const visibility = new IntersectionObserver(([entry]) => { slot.visible = entry.isIntersecting; if (slot.visible) { slot.dirty = true; this.invalidate(); } }, { rootMargin: '60px' }); visibility.observe(canvas);
    this.invalidate();
    return {
      update: next => {
        const key = packModelKey(next), viewKey = packViewKey(next);
        if (key === slot.key && viewKey === slot.viewKey) return;
        if (key !== slot.key) { slot.model?.dispose(); slot.model = undefined; slot.key = key; }
        if (next.revealing && !slot.options.revealing) slot.opening = performance.now();
        if (next.view.yaw !== slot.options.view.yaw || next.view.pitch !== slot.options.view.pitch) {
          slot.yaw = defaultYaw(next); slot.pitch = next.view.pitch ?? -3; slot.hoverX = slot.hoverY = slot.tiltX = slot.tiltY = 0;
        }
        if (next.reducedMotion) slot.hoverX = slot.hoverY = slot.tiltX = slot.tiltY = 0;
        slot.options = next; slot.viewKey = viewKey; slot.dirty = true; this.invalidate();
      },
      rotate: (yaw, pitch) => { slot.yaw = yaw; slot.pitch = Math.max(-75, Math.min(75, pitch)); slot.hoverX = slot.hoverY = slot.tiltX = slot.tiltY = 0; slot.dirty = true; this.invalidate(); },
      angles: () => ({ yaw: slot.yaw, pitch: slot.pitch }),
      hover: (x, y) => { if (slot.options.reducedMotion) return; const angles = hoverAngles(x, y); if (slot.hoverX === angles.yaw && slot.hoverY === angles.pitch) return; slot.hoverX = angles.yaw; slot.hoverY = angles.pitch; slot.dirty = true; this.invalidate(); },
      destroy: () => { resize.disconnect(); visibility.disconnect(); slot.model?.dispose(); this.slots.delete(slot); if (!this.slots.size) scheduleDispose(this); },
    };
  }
  dispose() {
    cancelAnimationFrame(this.frame);
    document.removeEventListener('visibilitychange', this.visibility);
    this.renderer.domElement.removeEventListener('webglcontextlost', this.contextLost);
    this.renderer.domElement.removeEventListener('webglcontextrestored', this.contextRestored);
    this.key.shadow.dispose(); this.contact.dispose();
    this.environment.dispose(); this.micro.dispose(); this.clay.dispose(); this.wire.dispose(); this.renderer.dispose(); this.renderer.forceContextLoss();
  }
}
let shared: PackRenderer | undefined;
let disposal = 0;
function scheduleDispose(renderer: PackRenderer) {
  window.clearTimeout(disposal);
  disposal = window.setTimeout(() => { if (shared === renderer && !renderer.slots.size) { renderer.dispose(); shared = undefined; } }, 1200);
}
export function attachPackRenderer(canvas: HTMLCanvasElement, options: PackRenderOptions, onReady: (ready: boolean) => void) {
  window.clearTimeout(disposal);
  shared ??= new PackRenderer();
  return shared.attach(canvas, options, onReady);
}

/** Development diagnostics: no model or renderer references escape this module. */
export function packRendererStats() {
  return { contexts: shared ? 1 : 0, views: shared?.slots.size ?? 0, renders: shared?.renders ?? 0,
    textures: shared?.renderer.info.memory.textures ?? 0, geometries: shared?.renderer.info.memory.geometries ?? 0,
    models: shared ? [...shared.slots].filter(slot => slot.model).map(slot => ({ id: slot.options.id, yaw: slot.yaw, pitch: slot.pitch,
      visible: slot.visible, surface: slot.options.view.surface ?? 'material',
      materials: Object.values(slot.model!.materials).map(material => ({ name: material.name, roughness: material.roughness, metalness: material.metalness })),
    })) : [] };
}
