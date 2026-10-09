import { expect, it } from 'vitest';
import { Box3, Euler, Matrix4, Quaternion, Vector3 } from 'three';
import { OBB } from 'three/addons/math/OBB.js';
import { openingPose, PACK_OPENING_PRESETS, hoverAngles } from './motion';
import { createContactShadow } from './contactShadow';

const transform = (position: number[], rotation: number[], scale = 1) => new Matrix4().compose(new Vector3(...position),
  new Quaternion().setFromEuler(new Euler(...rotation)), new Vector3(scale, scale, scale));
const cardBounds = (pose: ReturnType<typeof openingPose>['cards'][number]) =>
  new Box3(new Vector3(-.82, -1.14, -.013), new Vector3(.82, 1.14, .013)).applyMatrix4(transform(pose.position, pose.rotation, pose.scale));
function orientedBox(center: Vector3, halfSize: Vector3, matrix: Matrix4) {
  const box = new OBB(new Vector3(), halfSize).applyMatrix4(matrix);
  box.center.copy(center).applyMatrix4(matrix); return box;
}

it('tilts the face towards the pointer with a visible amplitude on both axes', () => {
  const angles = hoverAngles(.5, .5);
  expect(angles.yaw).toBe(14); expect(angles.pitch).toBe(11);
  const normal = new Vector3(0, 0, 1).applyEuler(new Euler(angles.pitch * Math.PI / 180, angles.yaw * Math.PI / 180, 0));
  expect(normal.x).toBeGreaterThan(0); expect(normal.y).toBeLessThan(0);
  expect(hoverAngles(-.5, -.5)).toEqual({ yaw: -14, pitch: -11 });
});

it('maps the contact shadow without mirroring its ground-plane direction', () => {
  const shadow = createContactShadow(); shadow.plane.updateMatrixWorld(); shadow.camera.updateMatrixWorld();
  for (const [u, v] of [[.2, .3], [.7, .8]]) {
    const world = new Vector3((u - .5) * 6, (v - .5) * 6, 0).applyMatrix4(shadow.plane.matrixWorld);
    const projected = world.project(shadow.camera);
    expect(projected.x * .5 + .5).toBeCloseTo(u, 6);
    expect(projected.y * .5 + .5).toBeCloseTo(v, 6);
  }
  shadow.dispose();
});

it('slides the whole paper stack through the mouth before fanning or tilting', () => {
  for (let frame = 0; frame <= 100; frame++) {
    const progress = frame / 100, motion = openingPose('paper', progress, 3);
    const flap = orientedBox(new Vector3(0, -.455, .006), new Vector3(1.145, .464, .014), transform(motion.closurePosition, motion.closureRotation));
    for (const card of motion.cards) {
      const bounds = cardBounds(card);
      if (bounds.min.y <= 1.63) {
        expect(bounds.min.x).toBeGreaterThan(-1.08); expect(bounds.max.x).toBeLessThan(1.08);
        expect(bounds.min.z).toBeGreaterThan(-.0735); expect(bounds.max.z).toBeLessThan(.0735);
        expect(card.rotation.every(angle => angle === 0)).toBe(true);
      }
      if (progress >= PACK_OPENING_PRESETS.paper.clear) expect(bounds.min.y).toBeGreaterThan(1.63);
      const stock = new OBB(new Vector3(), new Vector3(.82, 1.14, .013)).applyMatrix4(transform(card.position, card.rotation, card.scale));
      expect(stock.intersectsOBB(flap), `paper frame ${frame}, card z ${card.position[2]}`).toBe(false);
    }
  }
});

it('takes collector cards through the front opening without crossing the rim or lid', () => {
  for (let frame = 0; frame <= 100; frame++) {
    const motion = openingPose('collector', frame / 100, 3);
    const lid = orientedBox(new Vector3(0, 0, .006), new Vector3(1.19, 1.68, .12), transform(motion.closurePosition, motion.closureRotation));
    for (const card of motion.cards) {
      const bounds = cardBounds(card);
      if (bounds.min.z <= .36) {
        expect(bounds.min.x).toBeGreaterThan(-1.01); expect(bounds.max.x).toBeLessThan(1.01);
        expect(bounds.min.y).toBeGreaterThan(-1.50); expect(bounds.max.y).toBeLessThan(1.50);
      }
      const stock = new OBB(new Vector3(), new Vector3(.82, 1.14, .013)).applyMatrix4(transform(card.position, card.rotation, card.scale));
      expect(stock.intersectsOBB(lid)).toBe(false);
    }
  }
});

it.each(['standard', 'premium'] as const)('%s keeps cards aligned until clear, and does not shrink them back into the bag', packaging => {
  const preset = PACK_OPENING_PRESETS[packaging];
  for (let frame = 0; frame <= 100; frame++) {
    const progress = frame / 100, motion = openingPose(packaging, progress, 3);
    for (const card of motion.cards) {
      if (progress < preset.clear) expect(card.rotation.every(angle => angle === 0)).toBe(true);
      else expect(cardBounds(card).min.y).toBeGreaterThan(1.53);
    }
  }
});
