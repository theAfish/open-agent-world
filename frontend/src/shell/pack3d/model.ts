import { BufferGeometry, CylinderGeometry, Group, Material, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Vector3 } from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { boxWallGeometry, paperFlapGeometry, pouchGeometry } from './geometry';
import { createPrint, cardPrint } from './print';
import { createMicrostructure, createPackMaterials } from './materials';
import type { PackRenderOptions } from './types';
import { openingPose, ease } from './motion';

export function createPackModel(options: PackRenderOptions, micro: ReturnType<typeof createMicrostructure>, invalidate: () => void) {
  const root = new Group(), body = new Group(), closure = new Group(); root.add(body, closure);
  root.name = `pack:${options.packaging}`;
  const print = createPrint(options, invalidate), materials = createPackMaterials(options, print, micro);
  const cardPrints: ReturnType<typeof cardPrint>[] = [];
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
    const flap = paperFlapGeometry(), normals = flap.getAttribute('normal');
    flap.clearGroups();
    for (let i = 0; i < normals.count; i += 3) flap.addGroup(i, 3, normals.getZ(i) > .9 ? 0 : 1);
    // Coalesce adjacent groups so the flap does not create hundreds of draw calls.
    const groups = [...flap.groups]; flap.clearGroups();
    for (const group of groups) { const previous = flap.groups.at(-1); if (previous && previous.materialIndex === group.materialIndex && previous.start + previous.count === group.start) previous.count += group.count; else flap.addGroup(group.start, group.count, group.materialIndex); }
    const flapFace = materials.front.clone(); flapFace.map = print.flap; flapFace.roughnessMap = null;
    mesh(closure, flap, [flapFace, paper], 'arched-flap');
    closure.position.set(0, 1.61, .103);
    const stampMaterial = new MeshPhysicalMaterial({ color: '#e6dec7', roughness: .72, bumpMap: micro.paper, bumpScale: .006, name: 'paper-seal' });
    const stamp = mesh(closure, new CylinderGeometry(.16, .16, .023, 48), stampMaterial, 'embossed-seal', new Vector3(0, -.86, .033));
    stamp.rotation.x = Math.PI / 2;
    // A small leaf-shaped emboss, made from real curves on the paper seal.
    const leaf = new Mesh(new RoundedBoxGeometry(.025, .16, .012, 3, .012), new MeshStandardMaterial({ color: '#789071', roughness: .8 }));
    leaf.position.set(0, -.86, .052); leaf.rotation.z = -.35; closure.add(leaf);
    for (const sign of [-1, 1]) { const blade = leaf.clone(); blade.geometry = new RoundedBoxGeometry(.058, .10, .009, 4, .025); blade.position.set(sign * .035, -.85, .052); blade.rotation.z = sign * -.55; closure.add(blade); }
  }
  const originalMaterials = new Map<Mesh, Material | Material[]>(); root.traverse(object => { if (object instanceof Mesh) originalMaterials.set(object, object.material); });
  // Sealed packs and empty wrappers never show their cards. Prepare those
  // textures, geometries and materials only when a reveal actually needs them.
  const createCards = () => options.cards.slice(0, 3).map(card => {
    const cardMap = cardPrint(card, invalidate); cardPrints.push(cardMap);
    const edge = new MeshStandardMaterial({ color: '#e7e1d1', roughness: .85, name: 'card-stock' });
    const face = new MeshPhysicalMaterial({ map: cardMap.map, roughness: .73, clearcoat: .1, name: 'collected-card' });
    const back = materials.back.clone();
    const result = mesh(cardGroup, new RoundedBoxGeometry(1.64, 2.28, .026, 3, .045), [edge, edge, edge, edge, face, back], `card:${card.id}`);
    originalMaterials.set(result, result.material);
    return { mesh: result, face, stock: [face, edge, back], finish: card.finish };
  });
  let cardMeshes: ReturnType<typeof createCards> | undefined;
  function surface(override?: Material) { originalMaterials.forEach((material, object) => { object.material = override ?? material; }); }
  function pose(progress: number, revealing: boolean, finishVisible: boolean) {
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
    cardMeshes?.forEach(({ mesh: card, face, stock, finish }, i) => {
      const pose = motion.cards[i];
      card.position.fromArray(pose.position); card.rotation.set(...pose.rotation); card.scale.setScalar(pose.scale);
      stock.forEach(material => {
        const transparent = pose.opacity < 1;
        if (material.transparent !== transparent) { material.transparent = transparent; material.needsUpdate = true; }
        material.opacity = pose.opacity; material.depthWrite = !transparent;
      });
      const shiny = finishVisible && finish && finish !== 'normal';
      face.metalness = shiny ? .68 : 0; face.roughness = shiny ? .23 : .73;
      const iridescence = shiny && ['rainbow', 'laser'].includes(finish) ? .85 : 0;
      if (Boolean(face.iridescence) !== Boolean(iridescence)) face.needsUpdate = true;
      face.iridescence = iridescence;
    });
  }
  return { root, materials, pose, surface,
    dispose() {
      surface(); const geometries = new Set<BufferGeometry>(), mats = new Set<Material>();
      root.traverse(object => { if (object instanceof Mesh) { geometries.add(object.geometry); (Array.isArray(object.material) ? object.material : [object.material]).forEach(material => mats.add(material)); } });
      // A preset may not use every prepared material.
      Object.values(materials).forEach(material => mats.add(material));
      geometries.forEach(geometry => geometry.dispose()); mats.forEach(material => material.dispose());
      print.dispose(); cardPrints.forEach(print => print.dispose());
    },
  };
}
