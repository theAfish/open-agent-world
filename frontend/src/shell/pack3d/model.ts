import { BufferGeometry, CylinderGeometry, Group, Material, Mesh, MeshBasicMaterial, MeshPhysicalMaterial, MeshStandardMaterial, NoBlending, Vector3 } from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { boxWallGeometry, paperFlapGeometry, pouchGeometry } from './geometry';
import { createPrint } from './print';
import { createMicrostructure, createPackMaterials } from './materials';
import type { PackRenderOptions } from './types';
import { openingPose, ease } from './motion';
import { PACK_CARD_WIDTH, PACK_CARD_HEIGHT, PACK_CARD_DEPTH } from './cardProjection';

export function createPackModel(options: PackRenderOptions, micro: ReturnType<typeof createMicrostructure>, invalidate: () => void) {
  const root = new Group(), body = new Group(), closure = new Group(); root.add(body, closure);
  root.name = `pack:${options.packaging}`;
  const print = createPrint(options, invalidate), materials = createPackMaterials(options, print, micro);
  const cardGroup = new Group(); root.add(cardGroup);
  function mesh(parent: Group, geometry: BufferGeometry, material: Material | Material[], name: string, position?: Vector3) {
    const object = new Mesh(geometry, material); object.name = name;
    object.castShadow = object.receiveShadow = true;
    if (position) object.position.copy(position);
    parent.add(object); return object;
  }
  const parts = options.packaging;
  let lining: Mesh | undefined;
  let deformMouth: ((amount: number) => void) | undefined;
  if (parts === 'standard' || parts === 'premium') {
    const pouch = pouchGeometry(parts === 'premium');
    const position = pouch.getAttribute('position'), original = Float32Array.from(position.array);
    let lastMouth = -1;
    deformMouth = amount => {
      if (amount === lastMouth) return;
      lastMouth = amount;
      for (let i = 0; i < position.count; i++) {
        const x = original[i * 3], y = original[i * 3 + 1], z = original[i * 3 + 2];
        const lip = ease((y - 1.06) / .45) * Math.max(0, 1 - Math.pow(Math.abs(x) / 1.17, 8));
        position.setZ(i, z + Math.sign(z) * amount * .085 * lip);
      }
      position.needsUpdate = true; pouch.computeVertexNormals(); pouch.computeBoundingBox();
    };
    mesh(body, pouch, [materials.front, materials.back, materials.edge, materials.foil], 'inflated-shell');
    lining = mesh(body, pouch, materials.lining, 'foil-lining'); lining.scale.set(.997, .999, .96);
    const seal = pouchGeometry(parts === 'premium', 'seal');
    mesh(closure, seal, materials.edge, 'tear-strip');
  } else if (parts === 'collector') {
    // A rigid tray with a continuous hollow wall, a fitted base, and a separate cap.
    const wall = materials.edge.clone(); wall.name = 'rigid-board-wall'; wall.roughness = .78; wall.clearcoat = .15; wall.metalness = 0;
    mesh(body, boxWallGeometry(2.2, 3.18, .69, .075), wall, 'hollow-tray');
    const baseInside = new MeshStandardMaterial({ color: '#e9e3d4', roughness: .95, name: 'uncoated-interior' });
    mesh(body, new RoundedBoxGeometry(2.18, 3.16, .065, 3, .026), [wall, wall, wall, wall, baseInside, materials.back], 'tray-base', new Vector3(0, 0, -.34));
    const spine = wall.clone(); spine.map = print.spine; spine.color.set('#ffffff');
    mesh(body, new RoundedBoxGeometry(.014, 3.06, .6, 2, .004), spine, 'printed-spine', new Vector3(1.107, 0, -.015));
    mesh(closure, boxWallGeometry(2.36, 3.34, .17, .05), wall, 'lid-skirt', new Vector3(0, 0, -.02));
    mesh(closure, new RoundedBoxGeometry(2.37, 3.35, .065, 4, .028), [wall, wall, wall, wall, materials.front, baseInside], 'lid-panel', new Vector3(0, 0, .091));
    closure.position.z = .35;
  } else {
    const paper = materials.edge;
    const panel = new RoundedBoxGeometry(2.25, 3.23, .023, 3, .011);
    mesh(body, panel, [paper, paper, paper, paper, materials.front, paper], 'envelope-front', new Vector3(0, 0, .085));
    mesh(body, panel, [paper, paper, paper, paper, paper, materials.back], 'envelope-back', new Vector3(0, 0, -.085));
    mesh(body, new RoundedBoxGeometry(.026, 3.21, .17, 2, .012), paper, 'left-fold', new Vector3(-1.112, 0, 0));
    mesh(body, new RoundedBoxGeometry(.026, 3.21, .17, 2, .012), paper, 'right-fold', new Vector3(1.112, 0, 0));
    mesh(body, new RoundedBoxGeometry(2.23, .026, .17, 2, .012), paper, 'bottom-fold', new Vector3(0, -1.6, 0));
    // Wrap over the mouth and hinge at the rear crease, keeping the top fold
    // attached to the printed flap as both lift away from the card path.
    mesh(closure, new RoundedBoxGeometry(2.25, .023, .20, 3, .011), paper, 'top-fold', new Vector3(0, 0, .094));
    const flap = new Group(); flap.position.z = .188; closure.add(flap);
    const flapFace = materials.front.clone(); flapFace.map = print.flap; flapFace.roughnessMap = null;
    mesh(flap, paperFlapGeometry(), [flapFace, paper], 'arched-flap');
    closure.position.set(0, 1.61, -.085);
    const stampMaterial = new MeshPhysicalMaterial({ color: '#e6dec7', roughness: .72, bumpMap: micro.paper, bumpScale: .006, name: 'paper-seal' });
    const stamp = mesh(flap, new CylinderGeometry(.16, .16, .023, 48), stampMaterial, 'embossed-seal', new Vector3(0, -.86, .033));
    stamp.rotation.x = Math.PI / 2;
    // This small emboss needs rounded edges, but not inspection-scale subdivision.
    const leaf = new Mesh(new RoundedBoxGeometry(.025, .16, .012, 1, .012), new MeshStandardMaterial({ color: '#789071', roughness: .8 }));
    leaf.position.set(0, -.86, .052); leaf.rotation.z = -.35; flap.add(leaf);
    const bladeGeometry = new RoundedBoxGeometry(.058, .10, .009, 1, .025);
    for (const sign of [-1, 1]) { const blade = leaf.clone(); blade.geometry = bladeGeometry; blade.position.set(sign * .035, -.85, .052); blade.rotation.z = sign * -.55; flap.add(blade); }
  }
  const originalMaterials = new Map<Mesh, Material | Material[]>(); root.traverse(object => { if (object instanceof Mesh) originalMaterials.set(object, object.material); });
  // These depth masks let the real CardStock DOM show through the pack canvas.
  // They carry no card artwork or finish; the shared card components own both.
  const createCards = () => options.cards.slice(0, 3).map(card => {
    const edge = new MeshStandardMaterial({ color: '#e7e1d1', roughness: .85, name: 'card-stock' });
    // Alpha-zero faces must also write zero RGB into the premultiplied GPU buffer.
    const face = new MeshBasicMaterial({ color: '#000000', opacity: 0, blending: NoBlending, premultipliedAlpha: true, name: 'card-face-mask' });
    const result = mesh(cardGroup, new RoundedBoxGeometry(PACK_CARD_WIDTH, PACK_CARD_HEIGHT, PACK_CARD_DEPTH, 3, .045), [edge, edge, edge, edge, face, edge], `card:${card.id}`);
    originalMaterials.set(result, result.material);
    return { mesh: result, edge, opacity: 0 };
  });
  let cardMeshes: ReturnType<typeof createCards> | undefined;
  function surface(override?: Material) { originalMaterials.forEach((material, object) => { object.material = override ?? material; }); }
  function pose(progress: number, revealing: boolean) {
    const motion = openingPose(parts, progress, Math.min(options.cards.length, 3));
    closure.position.fromArray(motion.closurePosition); closure.rotation.set(...motion.closureRotation);
    closure.visible = motion.closureVisible;
    if (parts === 'standard' || parts === 'premium') {
      // The sealed shell completely occludes its interior, including in shadows.
      lining!.visible = progress > 0;
      body.scale.z = motion.bodyDepth; deformMouth?.(motion.mouth);
    }
    cardGroup.visible = revealing && progress > .01 && progress < 1;
    if (cardGroup.visible) cardMeshes ??= createCards();
    cardMeshes?.forEach((item, i) => {
      const { mesh: card, edge } = item;
      const pose = motion.cards[i];
      card.position.fromArray(pose.position); card.rotation.set(...pose.rotation); card.scale.setScalar(pose.scale);
      item.opacity = cardGroup.visible ? pose.opacity : 0;
      const transparent = pose.opacity < 1;
      if (edge.transparent !== transparent) { edge.transparent = transparent; edge.needsUpdate = true; }
      edge.opacity = pose.opacity; edge.depthWrite = !transparent;
    });
  }
  return { root, materials, pose, surface, get cards() { return cardMeshes ?? []; },
    dispose() {
      surface(); const geometries = new Set<BufferGeometry>(), mats = new Set<Material>();
      root.traverse(object => { if (object instanceof Mesh) { geometries.add(object.geometry); (Array.isArray(object.material) ? object.material : [object.material]).forEach(material => mats.add(material)); } });
      // A preset may not use every prepared material.
      Object.values(materials).forEach(material => mats.add(material));
      geometries.forEach(geometry => geometry.dispose()); mats.forEach(material => material.dispose());
      print.dispose();
    },
  };
}
